"""Dialogs: device properties editor (general info + interfaces)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    Device,
    DeviceStatus,
    Interface,
    InterfaceType,
    VlanMode,
    blank_mac,
)

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


class DevicePropertiesDialog(QDialog):
    """Edit everything about a device in one place, across two tabs:

    - **General**: device model, loopback IP, native VLAN, status
      (Active/Planned/Broken), and notes.
    - **Interfaces**: name, type, IP, MAC, VLAN mode, and VLAN(s) per port.

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
        vlans_item.setToolTip("Access: a single VLAN ID. Trunk: comma-separated VLAN IDs, e.g. 10,20,30")
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

            kwargs = dict(
                name=name, interface_type=itype, ip_address=ip, mac_address=mac,
                vlan_mode=vlan_mode, access_vlan=access_vlan, trunk_vlans=trunk_vlans,
            )
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
    label = QLabel("VLAN(s): a single ID for Access, or comma-separated IDs for Trunk (e.g. 10,20,30)")
    label.setStyleSheet("color: #666; font-size: 11px;")
    return label


# Backward-compatible alias: earlier code referred to this dialog as
# InterfacesDialog before it grew a General tab.
InterfacesDialog = DevicePropertiesDialog
