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
    GBPS,
    MBPS,
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
    best_unit_for,
    blank_mac,
    format_speed_mbps,
    format_speed_value,
    parse_speed_mbps,
    speed_from_type_label,
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
    return format_speed_mbps(mbps)


# Offered in the Speed column's dropdown, one shortlist per unit. They
# are a convenience, not a constraint: the field is editable, so
# anything parse_speed_mbps understands can be typed instead.
_SPEED_PRESETS_MBPS = [10, 100, 200, 500]
_SPEED_PRESETS_GBPS = [1_000, 2_500, 5_000, 10_000, 25_000, 40_000, 100_000]


class _UnitCombo(QComboBox):
    """Mbps/Gbps selector for one row of the interfaces table.

    Gbps is first, and so the default for a port with no figure of its
    own, because ports are specified in gigabits far more often than
    megabits — and because a bare "2.5" typed beside a 10 Gbps type
    means 2.5 Gbps to everyone except a parser that assumes Mbps.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem("Gbps", GBPS)
        self.addItem("Mbps", MBPS)

    def unit(self) -> int:
        """Mbps per displayed unit: 1000 for Gbps, 1 for Mbps."""
        data = self.currentData()
        return data if isinstance(data, int) else GBPS

    def set_unit(self, unit: int) -> None:
        index = self.findData(unit)
        if index >= 0:
            self.setCurrentIndex(index)


class _TypeCombo(QComboBox):
    """Media picker: the built-in classes, or a name you type.

    The presets carry a nominal rate, which is what the Speed column
    defers to. A typed name — "SFP28 DAC", "T1 serial", "DOCSIS 3.1" —
    is a label only, so the port keeps whichever preset was selected
    underneath it as the source of that default rate.

    Text that names a preset *is* that preset, however it got into the
    field. Making the box editable meant a name could arrive without the
    dropdown ever being used — typed by hand, completed inline by Qt, or
    pasted — and reading the preset from the selected index alone left
    the port on its old type while the cell displayed the new one. That
    silently discarded type changes and, with them, the link speeds
    derived from those types.
    """

    def __init__(self, itype: InterfaceType, label_override: str | None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for choice in _TYPE_CHOICES:
            self.addItem(choice.label, choice)
        self._base = itype
        self.setCurrentIndex(_TYPE_CHOICES.index(itype))
        if label_override:
            self.setEditText(label_override)
        self.currentIndexChanged.connect(self._on_preset_chosen)
        self.lineEdit().editingFinished.connect(self._normalize)

    def _on_preset_chosen(self, index: int) -> None:
        data = self.itemData(index)
        if isinstance(data, InterfaceType):
            self._base = data

    def _named_preset(self) -> InterfaceType | None:
        """The preset the current text names, if it names one."""
        text = self.currentText().strip()
        for choice in _TYPE_CHOICES:
            if text.casefold() == choice.label.casefold():
                return choice
        return None

    def base_type(self) -> InterfaceType:
        """The port's media class: what an unset speed falls back to.

        Reads the text first so a preset typed rather than picked still
        counts, then falls back to the last preset actually selected.
        """
        return self._named_preset() or self._base

    def label_override(self) -> str | None:
        """The typed name, or None when the text is just a preset."""
        text = self.currentText().strip()
        if not text or self._named_preset() is not None:
            return None
        return text

    def implied_speed_mbps(self, default_unit: int = GBPS) -> int | None:
        """The rate a typed name implies, if it implies one.

        Typing "2.5G" or "10GBASE-LR" into this column describes a port
        that runs at that rate, so the row's Speed follows it. Names
        with no rate in them — "SFP28", "T1 serial" — imply nothing and
        leave Speed alone.
        """
        label = self.label_override()
        if label is None:
            return None
        return speed_from_type_label(label, default_unit)

    def _normalize(self) -> None:
        """Turn typed text into a real selection where one applies.

        Selecting the item (rather than leaving matching text in the
        line edit) is what tells the Speed column which preset its
        Default entry now defers to.
        """
        if not self.currentText().strip():
            self.setCurrentIndex(_TYPE_CHOICES.index(self._base))
            return
        preset = self._named_preset()
        if preset is not None:
            self.setCurrentIndex(_TYPE_CHOICES.index(preset))


class _SpeedCombo(QComboBox):
    """Line-rate picker: common speeds, or type your own.

    Shows the number only; the Unit column beside it says what the
    number means, and a bare number is read in that unit. A written
    unit still wins, so "850M" works with Gbps selected.

    The first entry defers to the interface type, and its label follows
    the row's Type dropdown so the user can see what deferring means.
    Typed text is normalized when focus leaves the field: the figure is
    re-expressed in whichever unit reads better, so 2500 entered as
    Mbps comes back as 2.5 Gbps. Unparsable text reverts to the last
    good value rather than being quietly discarded at OK time.
    """

    def __init__(
        self,
        mbps: int | None,
        itype: InterfaceType,
        unit_combo: _UnitCombo,
        parent=None,
    ):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._unit_combo = unit_combo
        self._last_valid: int | None = mbps
        self._default_label = ""
        self._set_default_label(itype)
        if mbps is not None:
            unit_combo.set_unit(best_unit_for(mbps))
        self._rebuild_items()
        unit_combo.currentIndexChanged.connect(self._rebuild_items)
        self.lineEdit().editingFinished.connect(self._normalize)

    # ------------------------------------------------------------ contents
    def _set_default_label(self, itype: InterfaceType) -> None:
        rate = itype.speed_mbps
        self._default_label = (
            f"Default ({format_speed_mbps(rate)})" if rate else "Default (no fixed rate)"
        )

    def _rebuild_items(self) -> None:
        """Offer presets that make sense in the selected unit.

        Listing 100 Mbps as "0.1" under Gbps would be a worse menu than
        no menu, so each unit gets its own shortlist. Rebuilding also
        redraws the current figure, which is how switching units
        re-expresses it without changing what is stored.
        """
        keep = self._last_valid
        unit = self._unit_combo.unit()
        self.clear()
        self.addItem(self._default_label, None)
        for preset in (_SPEED_PRESETS_GBPS if unit == GBPS else _SPEED_PRESETS_MBPS):
            self.addItem(format_speed_value(preset, unit), preset)
        self._show(keep)

    def set_default_type(self, itype: InterfaceType) -> None:
        """Relabel the defer-to-type entry when the row's Type changes."""
        deferred = self._last_valid is None
        self._set_default_label(itype)
        self.setItemText(0, self._default_label)
        if deferred:
            self.setCurrentIndex(0)

    # -------------------------------------------------------------- values
    def _show(self, mbps: int | None) -> None:
        """Display a figure in the current unit, without reading it back."""
        self._last_valid = mbps
        if mbps is None:
            self.setCurrentIndex(0)
            return
        index = self.findData(mbps)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setEditText(format_speed_value(mbps, self._unit_combo.unit()))

    def set_mbps(self, mbps: int | None) -> None:
        """Display a figure, moving to whichever unit reads better for it."""
        self._last_valid = mbps
        if mbps is not None and self._unit_combo.unit() != best_unit_for(mbps):
            # The unit change rebuilds the list, which redraws the value.
            self._unit_combo.set_unit(best_unit_for(mbps))
            return
        self._show(mbps)

    def current_mbps(self) -> int | None:
        """The row's manual override, or None to defer to the type."""
        text = self.currentText().strip()
        if not text or text == self._default_label:
            self._last_valid = None
            return None
        try:
            self._last_valid = parse_speed_mbps(text, self._unit_combo.unit())
        except ValueError:
            logger.info("Ignoring unparsable interface speed %r", text)
        return self._last_valid

    def _normalize(self) -> None:
        self.set_mbps(self.current_mbps())


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


def _apply_implied_speed(
    type_combo: _TypeCombo, speed_combo: _SpeedCombo, unit_combo: _UnitCombo
) -> None:
    """Push a rate written into the Type column onto the Speed column."""
    implied = type_combo.implied_speed_mbps(unit_combo.unit())
    if implied is not None:
        speed_combo.set_mbps(implied)


class _InterfacesTab(QWidget):
    """Editable table of the device's ports: name, type, speed, unit, IP, MAC, VLAN.

    The VLAN Mode column picks Access or Trunk; the VLAN(s) column's
    meaning depends on the mode — a single VLAN ID for Access, or a
    comma-separated list of VLAN IDs for Trunk (e.g. "10,20,30").

    Speed defaults to whatever the Type implies, and can be overridden
    per port for the rates the presets do not cover.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Type", "Speed", "Unit", "IP address (CIDR)", "MAC address",
             "VLAN mode", "VLAN(s)"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        for iface in device.interfaces:
            self._append_row(
                iface.name, iface.interface_type, iface.type_label_override,
                iface.speed_mbps_override, iface.ip_address or "",
                iface.mac_address, iface.vlan_mode, _vlans_to_text(iface),
                iface.id,
            )

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("Add interface")
        add_btn.clicked.connect(
            lambda: self._append_row(
                "", InterfaceType.ETH_1G, None, None, "", blank_mac(),
                VlanMode.ACCESS, "1", None,
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
        type_label: str | None,
        speed_override: int | None,
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

        type_combo = _TypeCombo(itype, type_label)
        type_combo.setToolTip(
            "Media class for this port. Pick a preset, or type your own "
            "name — SFP28 DAC, T1 serial, DOCSIS 3.1. A custom name keeps "
            "the selected preset underneath as its default speed."
        )
        self.table.setCellWidget(row, 1, type_combo)

        unit_combo = _UnitCombo()
        unit_combo.setToolTip(
            "Unit the Speed number is given in. Gbps by default; a rate "
            "that reaches 1 Gbps is re-expressed in Gbps automatically."
        )
        speed_combo = _SpeedCombo(speed_override, itype, unit_combo)
        speed_combo.setToolTip(
            "Line rate for this port. Leave on Default to follow the Type "
            "column, pick a preset, or type a figure — read in the unit "
            "beside it, unless you write one (850M works under Gbps)."
        )
        # Keep the Default entry honest when the port's type changes.
        type_combo.currentIndexChanged.connect(
            lambda _index, combo=speed_combo, types=type_combo: combo.set_default_type(
                types.base_type()
            )
        )
        # A type typed as a rate ("2.5G", "10GBASE-LR") sets the row's
        # speed, so the change is visible in the table before OK and
        # reaches the links that derive from this port. Tracked as the
        # text changes rather than on focus loss: pressing OK straight
        # from the Type field never fires editingFinished, and a port
        # silently keeping its old rate is the whole bug this fixes.
        type_combo.editTextChanged.connect(
            lambda _text, combo=speed_combo, types=type_combo, units=unit_combo: (
                _apply_implied_speed(types, combo, units)
            )
        )
        self.table.setCellWidget(row, 2, speed_combo)
        self.table.setCellWidget(row, 3, unit_combo)

        self.table.setItem(row, 4, QTableWidgetItem(ip))
        self.table.setItem(row, 5, QTableWidgetItem(mac))

        vlan_mode_combo = QComboBox()
        for choice in _VLAN_MODE_CHOICES:
            vlan_mode_combo.addItem(choice.label, choice)
        vlan_mode_combo.setCurrentIndex(_VLAN_MODE_CHOICES.index(vlan_mode))
        self.table.setCellWidget(row, 6, vlan_mode_combo)

        vlans_item = QTableWidgetItem(vlans_text)
        vlans_item.setToolTip(
            "Access: a single VLAN ID. Trunk: comma-separated VLAN IDs, e.g. 10,20,30"
        )
        self.table.setItem(row, 7, vlans_item)

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
            ip_item = self.table.item(row, 4)
            mac_item = self.table.item(row, 5)
            vlans_item = self.table.item(row, 7)
            type_combo = self.table.cellWidget(row, 1)
            speed_combo = self.table.cellWidget(row, 2)
            vlan_mode_combo = self.table.cellWidget(row, 6)

            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            itype = (
                type_combo.base_type()
                if isinstance(type_combo, _TypeCombo)
                else InterfaceType.ETH_1G
            )
            type_label = (
                type_combo.label_override() if isinstance(type_combo, _TypeCombo) else None
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

            speed_override = (
                speed_combo.current_mbps() if isinstance(speed_combo, _SpeedCombo) else None
            )
            if speed_override is None and isinstance(type_combo, _TypeCombo):
                # The Speed column is still deferring, so a rate written
                # into the Type column is the port's rate. Checked here
                # as well as on editingFinished, because pressing OK
                # straight from the Type field never fires that.
                unit_combo = self.table.cellWidget(row, 3)
                unit = unit_combo.unit() if isinstance(unit_combo, _UnitCombo) else GBPS
                speed_override = type_combo.implied_speed_mbps(unit)

            kwargs = {
                "name": name,
                "interface_type": itype,
                "type_label_override": type_label,
                "speed_mbps_override": speed_override,
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
