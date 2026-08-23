"""Dialogs: device properties editor (general info + interfaces)."""

from __future__ import annotations

import logging
from typing import ClassVar

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from netplanner.domain.entities import (
    ConfigFile,
    Device,
    DeviceStatus,
    Interface,
    InterfaceType,
    Link,
    LinkType,
    Site,
    TextBox,
    VlanMode,
    blank_mac,
)
from netplanner.errors import ConfigImportError
from netplanner.export.styles import link_style_for
from netplanner.gui.config_viewer import ConfigViewerDialog

logger = logging.getLogger(__name__)

# Row order for the Type dropdown in the interfaces table.
_TYPE_CHOICES = [
    InterfaceType.ETH_1G,
    InterfaceType.ETH_10G,
    InterfaceType.ETH_25G,
    InterfaceType.ETH_100G,
    InterfaceType.WIRELESS,
]

# Row order for the VLAN Mode dropdown in the interfaces table.
_VLAN_MODE_CHOICES = [VlanMode.ACCESS, VlanMode.TRUNK]

# Row order for the Status dropdown in the General tab.
_STATUS_CHOICES = [DeviceStatus.ACTIVE, DeviceStatus.PLANNED, DeviceStatus.BROKEN]

VLAN_MIN, VLAN_MAX = 1, 4094  # valid 802.1Q VLAN ID range

# Bandwidth entry units and their Mbps multiplier. Storage stays Mbps;
# only the input is converted, so a plan saved in Gbps and reopened in
# Mbps holds the same number.
BANDWIDTH_UNITS = (("Mbps", 1), ("Gbps", 1000))


def _format_mbps(mbps: int) -> str:
    """Render a Mbps figure in whichever unit reads better."""
    if mbps >= 1000 and mbps % 1000 == 0:
        return f"{mbps // 1000} Gbps"
    if mbps >= 1000:
        return f"{mbps / 1000:g} Gbps"
    return f"{mbps} Mbps"


class DevicePropertiesDialog(QDialog):
    """Edit everything about a device in one place, across two tabs:

    - **General**: device model, loopback IP, native VLAN, status
      (Active/Planned/Broken), and notes.
    - **Interfaces**: name, type, IP, MAC, VLAN mode, and VLAN(s) per port.
    - **Configs**: attached configuration files, importable from disk and
      viewable in a read-only syntax-highlighted viewer.

    Existing interfaces keep their ids so links referencing them stay
    attached; new rows become brand-new Interface objects on accept.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {device.name}")
        self.resize(680, 440)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        self._general = _GeneralTab(device)
        tabs.addTab(self._general, "General")

        self._interfaces = _InterfacesTab(device)
        tabs.addTab(self._interfaces, "Interfaces")

        self._configs = _ConfigsTab(device)
        tabs.addTab(self._configs, f"Configs ({len(device.configs)})")

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # ----------------------------------------------------------------- result
    def result_device_model(self) -> str:
        return self._general.model_edit.text().strip()

    def result_loopback_ip(self) -> str | None:
        return self._general.loopback_edit.text().strip() or None

    def result_notes(self) -> str:
        return self._general.notes_edit.toPlainText()

    def result_native_vlan(self) -> int:
        return self._general.native_vlan_spin.value()

    def result_status(self) -> DeviceStatus:
        return self._general.status_combo.currentData()

    def result_interfaces(self) -> list[Interface]:
        return self._interfaces.result_interfaces()

    def result_configs(self) -> list[ConfigFile]:
        return self._configs.result_configs()


class _GeneralTab(QWidget):
    """Device model, loopback IP, native VLAN, status, and notes.

    All of these are shown by default on the card once set — native
    VLAN always shows (defaulting to 1, like an unconfigured switch)
    and status always shows via the card's color scheme (Active: type
    colors, Planned: type colors + diagonal stripes, Broken: grayed out).
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)

        self.model_edit = QLineEdit(device.device_model)
        self.model_edit.setPlaceholderText("e.g. Cisco ISR 4331, Mikrotik hAP ac2")
        form.addRow("Device model:", self.model_edit)

        self.loopback_edit = QLineEdit(device.loopback_ip or "")
        self.loopback_edit.setPlaceholderText("e.g. 10.255.0.1/32")
        form.addRow("Loopback IP:", self.loopback_edit)

        self.native_vlan_spin = QSpinBox()
        self.native_vlan_spin.setRange(VLAN_MIN, VLAN_MAX)
        self.native_vlan_spin.setValue(device.native_vlan)
        form.addRow("Native VLAN:", self.native_vlan_spin)

        self.status_combo = QComboBox()
        for choice in _STATUS_CHOICES:
            self.status_combo.addItem(choice.label, choice)
        self.status_combo.setCurrentIndex(_STATUS_CHOICES.index(device.status))
        form.addRow("Status:", self.status_combo)

        self.notes_edit = QPlainTextEdit(device.notes)
        self.notes_edit.setPlaceholderText("Free-form notes about this device...")
        form.addRow("Notes:", self.notes_edit)


class _InterfacesTab(QWidget):
    """Editable table of the device's ports: name, type, IP, MAC, VLAN.

    The VLAN Mode column picks Access or Trunk; the VLAN(s) column's
    meaning depends on the mode — a single VLAN ID for Access, or a
    comma-separated list of VLAN IDs for Trunk (e.g. "10,20,30").
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Type", "IP address (CIDR)", "MAC address", "VLAN mode", "VLAN(s)"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        for iface in device.interfaces:
            self._append_row(
                iface.name, iface.interface_type, iface.ip_address or "",
                iface.mac_address, iface.vlan_mode, _vlans_to_text(iface),
                iface.id,
            )

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("Add interface")
        add_btn.clicked.connect(
            lambda: self._append_row(
                "", InterfaceType.ETH_1G, "", blank_mac(), VlanMode.ACCESS, "1", None
            )
        )
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        buttons_row.addWidget(add_btn)
        buttons_row.addWidget(remove_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        layout.addWidget(_hint_label())

    # ------------------------------------------------------------ row helpers
    def _append_row(
        self,
        name: str,
        itype: InterfaceType,
        ip: str,
        mac: str,
        vlan_mode: VlanMode,
        vlans_text: str,
        iface_id: str | None,
    ) -> None:
        """Add one editable row; iface_id is stashed in UserRole for reuse.

        New rows start with an all-zeros placeholder MAC
        (see domain.entities.blank_mac) and access-mode VLAN 1; the
        user fills in real values, or leaves them as-is for a rough
        sketch.
        """
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, iface_id)
        self.table.setItem(row, 0, name_item)

        type_combo = QComboBox()
        for choice in _TYPE_CHOICES:
            type_combo.addItem(choice.label, choice)
        type_combo.setCurrentIndex(_TYPE_CHOICES.index(itype))
        self.table.setCellWidget(row, 1, type_combo)

        self.table.setItem(row, 2, QTableWidgetItem(ip))
        self.table.setItem(row, 3, QTableWidgetItem(mac))

        vlan_mode_combo = QComboBox()
        for choice in _VLAN_MODE_CHOICES:
            vlan_mode_combo.addItem(choice.label, choice)
        vlan_mode_combo.setCurrentIndex(_VLAN_MODE_CHOICES.index(vlan_mode))
        self.table.setCellWidget(row, 4, vlan_mode_combo)

        vlans_item = QTableWidgetItem(vlans_text)
        vlans_item.setToolTip(
            "Access: a single VLAN ID. Trunk: comma-separated VLAN IDs, e.g. 10,20,30"
        )
        self.table.setItem(row, 5, vlans_item)

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    # ----------------------------------------------------------------- result
    def result_interfaces(self) -> list[Interface]:
        """Collect the edited rows back into Interface objects.

        Rows with an empty name are silently dropped. VLAN(s) text is
        parsed according to the row's VLAN mode; invalid or
        out-of-range entries fall back to VLAN 1 / an empty trunk list
        rather than raising, since this is a free-text field.
        """
        result: list[Interface] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            ip_item = self.table.item(row, 2)
            mac_item = self.table.item(row, 3)
            vlans_item = self.table.item(row, 5)
            type_combo = self.table.cellWidget(row, 1)
            vlan_mode_combo = self.table.cellWidget(row, 4)

            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            itype = (
                type_combo.currentData()
                if isinstance(type_combo, QComboBox)
                else InterfaceType.ETH_1G
            )
            ip = (ip_item.text() if ip_item else "").strip() or None
            mac = (mac_item.text() if mac_item else "").strip() or blank_mac()
            vlan_mode = (
                vlan_mode_combo.currentData()
                if isinstance(vlan_mode_combo, QComboBox)
                else VlanMode.ACCESS
            )
            vlans_text = (vlans_item.text() if vlans_item else "").strip()
            access_vlan, trunk_vlans = _parse_vlans(vlan_mode, vlans_text)
            iface_id = name_item.data(Qt.ItemDataRole.UserRole)

            kwargs = {
                "name": name,
                "interface_type": itype,
                "ip_address": ip,
                "mac_address": mac,
                "vlan_mode": vlan_mode,
                "access_vlan": access_vlan,
                "trunk_vlans": trunk_vlans,
            }
            if iface_id:
                kwargs["id"] = iface_id
            result.append(Interface(**kwargs))
        return result


def _vlans_to_text(iface: Interface) -> str:
    """Render an interface's VLAN membership for the editable text cell."""
    if iface.vlan_mode is VlanMode.TRUNK:
        return ",".join(str(v) for v in iface.trunk_vlans)
    return str(iface.access_vlan)


def _parse_vlans(vlan_mode: VlanMode, text: str) -> tuple[int, list[int]]:
    """Parse the VLAN(s) cell into (access_vlan, trunk_vlans).

    Out-of-range or non-numeric entries are dropped silently rather than
    raising, since users may be mid-edit; a safe default (VLAN 1 /
    empty trunk list) is used when nothing valid was entered.
    """
    if vlan_mode is VlanMode.TRUNK:
        vlans = []
        for part in text.split(","):
            part = part.strip()
            if part.isdigit() and VLAN_MIN <= int(part) <= VLAN_MAX:
                vlans.append(int(part))
        return 1, vlans
    if text.isdigit() and VLAN_MIN <= int(text) <= VLAN_MAX:
        return int(text), []
    return 1, []


def _hint_label() -> QLabel:
    label = QLabel(
        "VLAN(s): a single ID for Access, or comma-separated IDs for Trunk (e.g. 10,20,30)"
    )
    label.setStyleSheet("color: #666; font-size: 11px;")
    return label


class _ConfigsTab(QWidget):
    """Attached configuration files: import, view, rename, remove.

    Files are copied into the plan on import, so the plan stays
    self-contained — a saved plan or exported .netplan carries its
    configs with it and remains readable on another machine.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.device = device
        # Work on a copy; the dialog only commits on OK.
        self._configs: list[ConfigFile] = list(device.configs)

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File name", "Format", "Size"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Double-clicking a row opens the viewer rather than editing in place.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._view_selected)
        layout.addWidget(self.table)

        buttons_row = QHBoxLayout()
        import_btn = QPushButton("Import config…")
        import_btn.clicked.connect(self._import_configs)
        view_btn = QPushButton("View")
        view_btn.clicked.connect(self._view_selected)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_selected)
        export_btn = QPushButton("Save a copy…")
        export_btn.clicked.connect(self._export_selected)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        for btn in (import_btn, view_btn, rename_btn, export_btn, remove_btn):
            buttons_row.addWidget(btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        hint = QLabel(
            "Configs are stored inside the plan. Double-click a file to open it. "
            "Viewing is read-only — re-import to update a file."
        )
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._refresh_table()

    # ------------------------------------------------------------ helpers
    def _refresh_table(self) -> None:
        """Rebuild the row list from self._configs."""
        self.table.setRowCount(0)
        for cfg in self._configs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(cfg.filename))
            self.table.setItem(row, 1, QTableWidgetItem(cfg.config_format.label))
            self.table.setItem(
                row, 2, QTableWidgetItem(f"{cfg.line_count} lines · {cfg.size_label}")
            )

    def _selected_index(self) -> int:
        """Row index of the current selection, or -1 when nothing is picked."""
        rows = {i.row() for i in self.table.selectedIndexes()}
        return min(rows) if rows else -1

    # ------------------------------------------------------------ actions
    def _import_configs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import configuration files",
            "",
            "Config files (*.cfg *.conf *.txt *.rsc *.boot);;All files (*)",
        )
        for path_str in paths:
            try:
                # Import via the controller helper so format detection and
                # tolerant decoding behave the same everywhere.
                from pathlib import Path

                from netplanner.app.controller import AppController

                self._configs.append(AppController.read_config_file(Path(path_str)))
            except (ConfigImportError, OSError) as exc:
                # read_config_file already wraps OSError as
                # ConfigImportError and logs the traceback; OSError stays
                # in the tuple so this keeps working if a future caller
                # path reads the file directly.
                logger.warning("Config import skipped for %s: %s", path_str, exc)
                QMessageBox.warning(self, "Import failed", f"Could not read {path_str}:\n\n{exc}")
        self._refresh_table()

    def _view_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        ConfigViewerDialog(self._configs[index], self.device.name, self).exec()

    def _rename_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        current = self._configs[index]
        name, ok = QInputDialog.getText(
            self, "Rename config", "File name:", text=current.filename
        )
        if ok and name.strip():
            current.filename = name.strip()
            self._refresh_table()

    def _export_selected(self) -> None:
        """Write a stored config back out to disk."""
        index = self._selected_index()
        if index < 0:
            return
        cfg = self._configs[index]
        path, _ = QFileDialog.getSaveFileName(self, "Save config copy", cfg.filename)
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(cfg.content)
            except OSError as exc:
                logger.exception("Config export failed writing %s", path)
                QMessageBox.warning(self, "Save failed", f"Could not write {path}:\n\n{exc}")

    def _remove_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        cfg = self._configs[index]
        confirm = QMessageBox.question(
            self,
            "Remove config",
            f"Remove '{cfg.filename}' from this device?\n\n"
            "The original file on disk is not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm is QMessageBox.StandardButton.Yes:
            del self._configs[index]
            self._refresh_table()

    def result_configs(self) -> list[ConfigFile]:
        return list(self._configs)


# Backward-compatible alias: earlier code referred to this dialog as
# InterfacesDialog before it grew a General tab.
InterfacesDialog = DevicePropertiesDialog


class TextBoxDialog(QDialog):
    """Edit a canvas text annotation: content, size, weight, color, width.

    All fields commit together as one undo step, matching how the device
    properties dialog behaves.
    """

    # Named colors keep the picker simple and the palette consistent with
    # the rest of the diagram (same hues as the device/link styles).
    COLOR_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("Default", "#1a1a1a"),
        ("Muted gray", "#5f6368"),
        ("Blue", "#1a56db"),
        ("Green", "#137333"),
        ("Red", "#c5221f"),
        ("Purple", "#7627bb"),
        ("Orange", "#b06000"),
    ]

    def __init__(self, textbox: TextBox, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text box")
        self.resize(460, 340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.text_edit = QPlainTextEdit(textbox.text)
        self.text_edit.setPlaceholderText(
            "Label a region, note a caveat, mark a planned change…"
        )
        layout.addWidget(QLabel("Text:"))
        layout.addWidget(self.text_edit)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 48)
        self.size_spin.setValue(int(textbox.font_size))
        self.size_spin.setSuffix(" pt")
        form.addRow("Font size:", self.size_spin)

        self.bold_check = QCheckBox("Bold")
        self.bold_check.setChecked(textbox.bold)
        form.addRow("Weight:", self.bold_check)

        self.color_combo = QComboBox()
        for name, value in self.COLOR_CHOICES:
            self.color_combo.addItem(name, value)
        existing = [v for _, v in self.COLOR_CHOICES].index(
            textbox.color
        ) if textbox.color in [v for _, v in self.COLOR_CHOICES] else 0
        self.color_combo.setCurrentIndex(existing)
        form.addRow("Color:", self.color_combo)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(60, 900)
        self.width_spin.setValue(int(textbox.width))
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip("Text wraps at this width; height follows the content.")
        form.addRow("Wrap width:", self.width_spin)

        layout.addLayout(form)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def result_text(self) -> str:
        return self.text_edit.toPlainText()

    def result_font_size(self) -> float:
        return float(self.size_spin.value())

    def result_bold(self) -> bool:
        return self.bold_check.isChecked()

    def result_color(self) -> str:
        return self.color_combo.currentData()

    def result_width(self) -> float:
        return float(self.width_spin.value())


class LinkPropertiesDialog(QDialog):
    """Edit a cable: its label, media type, and bandwidth.

    The label is the headline field — it's what shows on the diagram —
    so it takes focus when the dialog opens.
    """

    # Order shown in the media dropdown; matches the palette's order.
    LINK_TYPE_CHOICES: ClassVar[list[LinkType]] = [
        LinkType.ETHERNET,
        LinkType.FIBER,
        LinkType.WIRELESS,
        LinkType.SERIAL,
        LinkType.WAN,
    ]

    def __init__(
        self,
        link: Link,
        endpoints: str = "",
        derived_speed_mbps: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Link properties")
        self.resize(420, 200)

        layout = QVBoxLayout(self)

        if endpoints:
            header = QLabel(endpoints)
            header.setStyleSheet("color: #444; font-weight: bold;")
            layout.addWidget(header)

        form = QFormLayout()

        self.label_edit = QLineEdit(link.label)
        self.label_edit.setPlaceholderText("e.g. Core uplink, MPLS circuit 4471")
        form.addRow("Label:", self.label_edit)

        self.type_combo = QComboBox()
        for choice in self.LINK_TYPE_CHOICES:
            # Media names live on the style table, not the enum, so the
            # dropdown reads the same as the palette does.
            self.type_combo.addItem(link_style_for(choice).label, choice)
        if link.link_type in self.LINK_TYPE_CHOICES:
            self.type_combo.setCurrentIndex(self.LINK_TYPE_CHOICES.index(link.link_type))
        form.addRow("Media:", self.type_combo)

        # Bandwidth is stored in Mbps but entered in whichever unit suits
        # the number: 500 Mbps and 100 Gbps are both natural to type, and
        # forcing one unit makes the other awkward.
        self.bandwidth_spin = QDoubleSpinBox()
        self.bandwidth_spin.setDecimals(0)
        self.bandwidth_spin.setRange(0, 400_000)
        # 0 doubles as "unset": a spinbox has no empty state, and a link
        # with no recorded bandwidth is normal rather than an error.
        self.bandwidth_spin.setSpecialValueText("not set")

        self.unit_combo = QComboBox()
        for name, factor in BANDWIDTH_UNITS:
            self.unit_combo.addItem(name, factor)
        # Default to whichever unit renders the stored value most
        # readably: 10000 Mbps reads better as 10 Gbps.
        default_unit = 1 if (link.bandwidth_mbps or 0) >= 1000 else 0
        self.unit_combo.setCurrentIndex(default_unit)
        self._apply_unit(default_unit, link.bandwidth_mbps)
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)

        bandwidth_row = QHBoxLayout()
        bandwidth_row.addWidget(self.bandwidth_spin)
        bandwidth_row.addWidget(self.unit_combo)

        bandwidth_row.addStretch()
        form.addRow("Bandwidth:", bandwidth_row)

        # Auto-tracking: while ticked, the speed follows the slower of
        # the two interfaces and updates when either port's type
        # changes. Typing a figure by hand unticks it, so a measured or
        # contracted rate is never overwritten by a later port edit.
        self._derived = derived_speed_mbps
        derived_text = _format_mbps(derived_speed_mbps) if derived_speed_mbps else "not available"
        self.auto_check = QCheckBox(f"Track interface speeds (currently {derived_text})")
        self.auto_check.setToolTip(
            "Keep bandwidth equal to the slower of the two connected "
            "interfaces, updating automatically if either port's type changes."
        )
        self.auto_check.setEnabled(derived_speed_mbps is not None)
        self.auto_check.setChecked(bool(link.bandwidth_auto) and derived_speed_mbps is not None)
        self.auto_check.toggled.connect(self._on_auto_toggled)
        form.addRow("", self.auto_check)

        # Manual entry is what clears the flag; wiring it here rather
        # than in _set_mbps keeps programmatic updates from unticking.
        self.bandwidth_spin.valueChanged.connect(self._on_bandwidth_typed)
        self._on_auto_toggled(self.auto_check.isChecked())

        layout.addLayout(form)

        hint = QLabel("The label is drawn on the cable, on the canvas and in exports.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.label_edit.setFocus()

    def result_label(self) -> str:
        return self.label_edit.text().strip()

    def result_link_type(self) -> LinkType:
        return self.type_combo.currentData()

    # ------------------------------------------------------------ bandwidth
    def _apply_unit(self, index: int, mbps: int | None) -> None:
        """Configure the spinbox for a unit and show `mbps` in it."""
        _, factor = BANDWIDTH_UNITS[index]
        # Remember the unit the displayed number is currently in. On a
        # unit change the combo has already moved, so converting with
        # currentData() would use the new factor against the old number
        # and silently divide the value by 1000.
        self._display_factor = factor
        self.bandwidth_spin.setSuffix(f" {BANDWIDTH_UNITS[index][0]}")
        self.bandwidth_spin.setRange(0, 400_000 / factor)
        # Gbps needs decimals to express sub-gigabit links (500 Mbps).
        self.bandwidth_spin.setDecimals(0 if factor == 1 else 2)
        self.bandwidth_spin.setValue((mbps or 0) / factor)

    def _on_unit_changed(self, index: int) -> None:
        """Switch units, preserving the value rather than the number."""
        current = round(self.bandwidth_spin.value() * self._display_factor) or None
        self._apply_unit(index, current)

    def _current_mbps(self) -> int | None:
        """The field's value in Mbps, whatever unit it's displayed in."""
        return round(self.bandwidth_spin.value() * self._display_factor) or None

    def _set_mbps(self, mbps: int) -> None:
        """Set the field from a Mbps figure, picking a readable unit."""
        index = 1 if mbps >= 1000 else 0
        self.unit_combo.setCurrentIndex(index)
        self._apply_unit(index, mbps)

    def _on_auto_toggled(self, checked: bool) -> None:
        """Lock the field to the derived rate while tracking is on."""
        self.bandwidth_spin.setReadOnly(checked)
        self.bandwidth_spin.setEnabled(not checked)
        self.unit_combo.setEnabled(not checked)
        if checked and self._derived:
            self._set_mbps(self._derived)

    def _on_bandwidth_typed(self, _value: float) -> None:
        """A hand-typed figure means the user owns this number now."""
        if self.auto_check.isChecked() and self.bandwidth_spin.isEnabled():
            self.auto_check.setChecked(False)

    def result_bandwidth_auto(self) -> bool:
        return self.auto_check.isChecked()

    def result_bandwidth(self) -> int | None:
        if self.auto_check.isChecked() and self._derived:
            return self._derived
        return self._current_mbps()  # 0 means "not set"


class SiteDialog(QDialog):
    """Edit a site box: its name, notes, and colour."""

    # Same hues as the text box palette, so annotations and sites read
    # as one visual family.
    COLOR_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("Blue", "#1a73e8"),
        ("Green", "#137333"),
        ("Purple", "#7627bb"),
        ("Orange", "#b06000"),
        ("Red", "#c5221f"),
        ("Teal", "#00838f"),
        ("Gray", "#5f6368"),
    ]

    def __init__(self, site: Site, contained: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Site")
        self.resize(460, 340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(site.name)
        self.name_edit.setPlaceholderText("e.g. IDF 1, HQ Server Room, Rack 12")
        form.addRow("Name:", self.name_edit)

        self.color_combo = QComboBox()
        for name, value in self.COLOR_CHOICES:
            self.color_combo.addItem(name, value)
        values = [v for _, v in self.COLOR_CHOICES]
        self.color_combo.setCurrentIndex(values.index(site.color) if site.color in values else 0)
        form.addRow("Colour:", self.color_combo)

        layout.addLayout(form)

        layout.addWidget(QLabel("Notes:"))
        self.notes_edit = QPlainTextEdit(site.notes)
        self.notes_edit.setPlaceholderText(
            "Address, rack numbers, access instructions, contacts…"
        )
        layout.addWidget(self.notes_edit)

        # Membership is positional, so tell the user what the box
        # currently covers rather than making them count devices.
        summary = QLabel(
            f"{contained} device(s) currently sit inside this site's box. "
            "Membership follows position — drag equipment in or out to change it."
        )
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(summary)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.name_edit.setFocus()

    def result_name(self) -> str:
        return self.name_edit.text().strip()

    def result_notes(self) -> str:
        return self.notes_edit.toPlainText()

    def result_color(self) -> str:
        return self.color_combo.currentData()
