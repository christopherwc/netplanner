"""Dialogs: device properties editor (general info + interfaces)."""

from __future__ import annotations

import logging
from typing import ClassVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
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

from netplanner.domain.config_interfaces import ParsedInterface, mirror_interfaces, parse_interfaces
from netplanner.domain.entities import (
    DEFAULT_MAX_SPEED_MBPS,
    GBPS,
    MBPS,
    ConfigFile,
    Device,
    DeviceStatus,
    Interface,
    Link,
    LinkType,
    Site,
    TextBox,
    VlanMode,
    best_unit_for,
    blank_mac,
    format_speed_mbps,
    format_speed_value,
    negotiate_rates,
)
from netplanner.errors import ConfigImportError
from netplanner.export.styles import link_style_for
from netplanner.gui.config_viewer import ConfigViewerDialog
from netplanner.gui.qtutil import required

logger = logging.getLogger(__name__)

# Column layout of the interfaces table. Named rather than numbered
# because the rate, its unit and the negotiated figure read as a group
# and have to stay adjacent: hand-renumbering them is how a cell ends up
# wired to the widget next door.
COL_NAME = 0
COL_MAX_SPEED = 1
COL_UNIT = 2
COL_NEGOTIATED = 3
COL_IP = 4
COL_MAC = 5
COL_VLAN_MODE = 6
COL_VLANS = 7

_COLUMN_LABELS = [
    "Name",
    "Maximum Interface Speed",
    "Unit",
    "Negotiated",
    "IP address (CIDR)",
    "MAC address",
    "VLAN mode",
    "VLAN(s)",
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
        self._previous = GBPS

    def unit(self) -> int:
        """Mbps per displayed unit: 1000 for Gbps, 1 for Mbps."""
        data = self.currentData()
        return data if isinstance(data, int) else GBPS

    def previous_unit(self) -> int:
        """The unit in force before the current selection.

        Switching units re-expresses the rate beside it rather than
        rescaling it, and re-expressing needs to know what the number
        on screen meant a moment ago.
        """
        return self._previous

    def remember_unit(self) -> None:
        """Mark the current unit as the one a later switch moves from."""
        self._previous = self.unit()

    def set_unit(self, unit: int) -> None:
        index = self.findData(unit)
        if index >= 0:
            self.setCurrentIndex(index)
        self.remember_unit()


class _SpeedEdit(QLineEdit):
    """The port's maximum rate, as a bare number in the row's unit.

    A number and nothing else. The unit is the selector beside it, so
    there is no spelling to parse and nothing to guess: 2.5 next to Gbps
    is 2.5 Gbps, and cannot be read as anything else.

    Blank is a real value, not an empty one. It means the rate has not
    been established — a licensed radio nobody has measured, a handoff
    whose contracted rate is not known yet — and it is carried as
    unknown rather than quietly filled in with a plausible figure.
    """

    def __init__(self, mbps: int | None, unit: int, parent=None):
        super().__init__(parent)
        # Six decimals is far past anything real; it exists so that
        # re-expressing a rate in Mbps never truncates what Gbps held.
        validator = QDoubleValidator(0.0, 1_000_000.0, 6, self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.setValidator(validator)
        self.setPlaceholderText("unknown")
        self.set_mbps(mbps, unit)

    def mbps(self, unit: int) -> int | None:
        """The rate in Mbps, or None when the field is blank.

        Anything that does not resolve to at least 1 Mbps reads as
        unknown. The validator keeps out non-numbers, so the remaining
        cases are a blank field and a figure rounding to zero.
        """
        text = self.text().strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        mbps = round(value * unit)
        return mbps if mbps >= 1 else None

    def set_mbps(self, mbps: int | None, unit: int) -> None:
        """Show a stored rate in `unit`, or clear the field for None."""
        self.setText("" if mbps is None else format_speed_value(mbps, unit))


class DevicePropertiesDialog(QDialog):
    """Edit everything about a device in one place, across two tabs:

    - **General**: device model, loopback IP, native VLAN, status
      (Active/Planned/Broken), and notes.
    - **Interfaces**: name, type, IP, MAC, VLAN mode, and VLAN(s) per
      port, plus the rate each port will negotiate. `peer_speeds` maps
      interface id to the rate of the port at the far end of its link,
      which is what makes that figure real rather than a guess; without
      it every port simply shows its own rate.
    - **Configs**: attached configuration files, importable from disk and
      viewable in a read-only syntax-highlighted viewer.

    Existing interfaces keep their ids so links referencing them stay
    attached; new rows become brand-new Interface objects on accept.
    """

    def __init__(
        self,
        device: Device,
        peer_speeds: dict[str, int | None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {device.name}")
        self.resize(680, 440)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        self._general = _GeneralTab(device)
        tabs.addTab(self._general, "General")

        self._interfaces = _InterfacesTab(device, peer_speeds)
        tabs.addTab(self._interfaces, "Interfaces")

        self._configs = _ConfigsTab(device)
        tabs.addTab(self._configs, f"Configs ({len(device.configs)})")
        self._configs.sync_requested.connect(self._sync_interfaces_from_config)

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

    # -------------------------------------------------------------- syncing
    def _sync_interfaces_from_config(self, config: ConfigFile) -> None:
        """Parse the config picked in the Configs tab and merge its
        interface data into the Interfaces tab's working copy.

        Applied to the dialog's in-memory state, not committed via the
        controller — so it becomes part of the single undo step OK
        already produces for this dialog, and Cancel discards it along
        with every other edit.
        """
        parsed = parse_interfaces(config.content, config.config_format)
        if not parsed:
            QMessageBox.information(
                self,
                "Sync interfaces",
                f"No interface configuration was recognized in '{config.filename}'.",
            )
            return
        touched = self._interfaces.apply_parsed_interfaces(parsed)
        plural = "s" if touched != 1 else ""
        QMessageBox.information(
            self,
            "Sync interfaces",
            f"Synced {touched} interface{plural} from '{config.filename}'.",
        )


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
    """Editable table of the device's ports: rate, IP, MAC, VLAN.

    The VLAN Mode column picks Access or Trunk; the VLAN(s) column's
    meaning depends on the mode — a single VLAN ID for Access, or a
    comma-separated list of VLAN IDs for Trunk (e.g. "10,20,30").

    Maximum Interface Speed is what the port can do on its own, entered
    as a plain number in whatever the Unit column says. Negotiated sits
    next to them and is not an input: it is what the port will actually
    run at once the far end has had its say, recomputed as the rate and
    the unit are edited.
    """

    def __init__(
        self,
        device: Device,
        peer_speeds: dict[str, int | None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        # interface id -> the rate of the port at the other end of its
        # link, or None when nothing is patched in.
        self._peer_speeds = peer_speeds or {}

        self.table = QTableWidget(0, len(_COLUMN_LABELS))
        self.table.setHorizontalHeaderLabels(_COLUMN_LABELS)
        required(self.table.horizontalHeader(), "table header").setStretchLastSection(True)
        layout.addWidget(self.table)

        for iface in device.interfaces:
            self._append_row(
                iface.name, iface.max_speed_mbps, iface.ip_address or "",
                iface.mac_address, iface.vlan_mode, _vlans_to_text(iface),
                iface.id,
            )

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("Add interface")
        add_btn.clicked.connect(
            lambda: self._append_row(
                "", DEFAULT_MAX_SPEED_MBPS, "", blank_mac(),
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
        max_speed_mbps: int | None,
        ip: str,
        mac: str,
        vlan_mode: VlanMode,
        vlans_text: str,
        iface_id: str | None,
    ) -> None:
        """Add one editable row; iface_id is stashed in UserRole for reuse.

        New rows start at one gigabit, with an all-zeros placeholder MAC
        (see domain.entities.blank_mac) and access-mode VLAN 1; the user
        fills in real values, or leaves them as-is for a rough sketch.
        """
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, iface_id)
        self.table.setItem(row, COL_NAME, name_item)

        # A rate is shown in the unit it reads best in, so a 1000 Mbps
        # port opens as "1" beside Gbps rather than "1000" beside Mbps.
        unit = best_unit_for(max_speed_mbps) if max_speed_mbps is not None else GBPS
        unit_combo = _UnitCombo()
        unit_combo.set_unit(unit)
        unit_combo.setToolTip(
            "Unit for this row's maximum and negotiated figures. Gbps by "
            "default; switching re-expresses the rate rather than changing it."
        )

        speed_edit = _SpeedEdit(max_speed_mbps, unit)
        speed_edit.setToolTip(
            "The fastest this port can run, in the unit beside it. Leave "
            "blank when the rate is not known — a radio nobody has "
            "measured — and nothing will be assumed on its behalf."
        )
        self.table.setCellWidget(row, COL_MAX_SPEED, speed_edit)
        self.table.setCellWidget(row, COL_UNIT, unit_combo)

        negotiated_item = QTableWidgetItem()
        # Derived, so not editable: what this port runs at is decided by
        # its own maximum and the far end's, never typed here.
        negotiated_item.setFlags(negotiated_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        negotiated_item.setToolTip(
            "What this port will run at: the slower of its own maximum and "
            "the maximum of the port it is patched into."
        )
        self.table.setItem(row, COL_NEGOTIATED, negotiated_item)

        # Both figures follow the inputs as they are edited, so the row
        # always shows what pressing OK would produce. The handlers find
        # their row from the widget rather than closing over the index,
        # which would go stale the moment a row above them is removed.
        speed_edit.textChanged.connect(
            lambda _text, w=speed_edit: self._refresh_negotiated(
                self._row_of(w, COL_MAX_SPEED)
            )
        )
        unit_combo.currentIndexChanged.connect(
            lambda _index, w=unit_combo: self._on_unit_changed(w)
        )

        self.table.setItem(row, COL_IP, QTableWidgetItem(ip))
        self.table.setItem(row, COL_MAC, QTableWidgetItem(mac))

        vlan_mode_combo = QComboBox()
        for choice in _VLAN_MODE_CHOICES:
            vlan_mode_combo.addItem(choice.label, choice)
        vlan_mode_combo.setCurrentIndex(_VLAN_MODE_CHOICES.index(vlan_mode))
        self.table.setCellWidget(row, COL_VLAN_MODE, vlan_mode_combo)

        vlans_item = QTableWidgetItem(vlans_text)
        vlans_item.setToolTip(
            "Access: a single VLAN ID. Trunk: comma-separated VLAN IDs, e.g. 10,20,30"
        )
        self.table.setItem(row, COL_VLANS, vlans_item)

        self._refresh_negotiated(row)  # draw the figure the row starts with

    def _row_of(self, widget: QWidget, column: int) -> int:
        """Which row holds `widget` in `column`, or -1 if it has gone.

        Row indices shift when a row above is removed, so a signal
        handler cannot hold one. Looking the widget up at signal time is
        the only index that is right when it is used.
        """
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, column) is widget:
                return row
        return -1

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _on_unit_changed(self, unit_combo: _UnitCombo) -> None:
        """Re-express this row's maximum in the newly chosen unit.

        Switching the selector is a change of how the rate is written,
        not of the rate: 2.5 under Gbps becomes 2500 under Mbps, and the
        port still runs at the same speed. Rescaling instead — leaving
        2.5 in place to mean 2.5 Mbps — would silently cut a port to a
        thousandth of its rate for the price of one dropdown.
        """
        row = self._row_of(unit_combo, COL_UNIT)
        if row < 0:
            return
        speed_edit = self.table.cellWidget(row, COL_MAX_SPEED)
        # Always a _SpeedEdit: _append_row is the only thing that fills
        # this column. The check is a type narrowing for the lines
        # below, not a case that occurs.
        if isinstance(speed_edit, _SpeedEdit):
            mbps = speed_edit.mbps(unit_combo.previous_unit())
            speed_edit.set_mbps(mbps, unit_combo.unit())
        unit_combo.remember_unit()
        self._refresh_negotiated(row)

    # ----------------------------------------------------------------- result
    def _row_rate_mbps(self, row: int) -> int | None:
        """The maximum this row's port states for itself, in Mbps."""
        speed_edit = self.table.cellWidget(row, COL_MAX_SPEED)
        unit_combo = self.table.cellWidget(row, COL_UNIT)
        if not isinstance(speed_edit, _SpeedEdit):
            return None
        unit = unit_combo.unit() if isinstance(unit_combo, _UnitCombo) else GBPS
        return speed_edit.mbps(unit)

    def _refresh_negotiated(self, row: int) -> None:
        """Redraw one row's negotiated figure from the current inputs."""
        if row < 0:
            return
        item = self.table.item(row, COL_NEGOTIATED)
        unit_combo = self.table.cellWidget(row, COL_UNIT)
        name_item = self.table.item(row, COL_NAME)
        if item is None or name_item is None:
            return

        iface_id = name_item.data(Qt.ItemDataRole.UserRole)
        own = self._row_rate_mbps(row)
        peer = self._peer_speeds.get(iface_id)
        negotiated = negotiate_rates(own, peer)

        if negotiated is None:
            # A port with no rate of its own, patched into nothing or
            # into another port with no rate: there is nothing to state.
            item.setText("—")
            return
        # Shown in the row's own unit, not whichever unit reads best.
        # Choosing the unit here would move the selector under the
        # person editing the maximum beside it.
        unit = unit_combo.unit() if isinstance(unit_combo, _UnitCombo) else GBPS
        item.setText(format_speed_value(negotiated, unit))

    def result_interfaces(self) -> list[Interface]:
        """Collect the edited rows back into Interface objects.

        Rows with an empty name are silently dropped. VLAN(s) text is
        parsed according to the row's VLAN mode; invalid or
        out-of-range entries fall back to VLAN 1 / an empty trunk list
        rather than raising, since this is a free-text field.
        """
        result: list[Interface] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, COL_NAME)
            ip_item = self.table.item(row, COL_IP)
            mac_item = self.table.item(row, COL_MAC)
            vlans_item = self.table.item(row, COL_VLANS)
            vlan_mode_combo = self.table.cellWidget(row, COL_VLAN_MODE)

            # Skipped outright rather than read through a ternary: a row
            # with no name cell is not a port, and returning early is
            # also what lets the id below be read without a second guard.
            if name_item is None:
                continue
            name = name_item.text().strip()
            if not name:
                continue
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

            # Built directly rather than splatted from a dict: a dict
            # of mixed value types has no per-key type, so **kwargs
            # discards every field's type on the way into Interface.
            interface = Interface(
                name=name,
                max_speed_mbps=self._row_rate_mbps(row),
                ip_address=ip,
                mac_address=mac,
                vlan_mode=vlan_mode,
                access_vlan=access_vlan,
                trunk_vlans=trunk_vlans,
            )
            # An id only exists for a row that came from a saved port;
            # a row the user just added gets the one Interface made.
            if iface_id:
                interface.id = iface_id
            result.append(interface)
        return result

    def apply_parsed_interfaces(self, parsed: list[ParsedInterface]) -> int:
        """Merge parsed config values into the table (Sync from the
        Configs tab): a matching row's IP/VLAN cells are overwritten,
        an unmatched parsed interface becomes a new row. Rebuilds the
        table from result_interfaces() + mirror_interfaces() rather
        than patching cells in place, so this stays in lockstep with
        the exact same matching and merge rules a headless caller gets
        from config_interfaces directly. Returns how many parsed
        interfaces were applied (matched or added).
        """
        mirrored = mirror_interfaces(self.result_interfaces(), parsed)
        self.table.setRowCount(0)
        for iface in mirrored:
            self._append_row(
                iface.name, iface.max_speed_mbps, iface.ip_address or "",
                iface.mac_address, iface.vlan_mode, _vlans_to_text(iface),
                iface.id,
            )
        return len(parsed)


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

    # Emitted with the selected ConfigFile when "Sync interfaces" is
    # clicked. This tab only has a Device reference, not the sibling
    # Interfaces tab, so the merge itself happens in
    # DevicePropertiesDialog, which owns both.
    sync_requested = pyqtSignal(object)

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.device = device
        # Work on a copy; the dialog only commits on OK.
        self._configs: list[ConfigFile] = list(device.configs)

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File name", "Format", "Size"])
        required(self.table.horizontalHeader(), "table header").setStretchLastSection(True)
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
        sync_btn = QPushButton("Sync interfaces from this…")
        sync_btn.setToolTip(
            "Read this config's interface names, IP addresses, and VLAN "
            "membership (Cisco and MikroTik) and apply them to the "
            "Interfaces tab. Matched by interface name; interfaces the "
            "config doesn't mention are left alone."
        )
        sync_btn.clicked.connect(self._sync_interfaces_from_selected)
        for btn in (import_btn, view_btn, rename_btn, export_btn, remove_btn, sync_btn):
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

    def _sync_interfaces_from_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        self.sync_requested.emit(self._configs[index])

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
