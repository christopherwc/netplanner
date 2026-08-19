"""Dialogs: device properties editor (general info + interfaces)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from netplanner.domain.entities import Device, Interface, InterfaceType, blank_mac

# Row order for the Type dropdown in the interfaces table.
_TYPE_CHOICES = [
    InterfaceType.ETH_1G,
    InterfaceType.ETH_10G,
    InterfaceType.ETH_25G,
    InterfaceType.ETH_100G,
    InterfaceType.WIRELESS,
]


class DevicePropertiesDialog(QDialog):
    """Edit everything about a device in one place, across two tabs:

    - **General**: device model, loopback IP, and free-form notes.
    - **Interfaces**: name, type (speed/wireless), IP, and MAC per port.

    Existing interfaces keep their ids so links referencing them stay
    attached; new rows become brand-new Interface objects on accept.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {device.name}")
        self.resize(560, 420)

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

    def result_interfaces(self) -> list[Interface]:
        return self._interfaces.result_interfaces()


class _GeneralTab(QWidget):
    """Device model, loopback IP, and notes — all shown by default on the card."""

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)

        self.model_edit = QLineEdit(device.device_model)
        self.model_edit.setPlaceholderText("e.g. Cisco ISR 4331, Mikrotik hAP ac2")
        form.addRow("Device model:", self.model_edit)

        self.loopback_edit = QLineEdit(device.loopback_ip or "")
        self.loopback_edit.setPlaceholderText("e.g. 10.255.0.1/32")
        form.addRow("Loopback IP:", self.loopback_edit)

        self.notes_edit = QPlainTextEdit(device.notes)
        self.notes_edit.setPlaceholderText("Free-form notes about this device...")
        form.addRow("Notes:", self.notes_edit)


class _InterfacesTab(QWidget):
    """Editable table of the device's ports (name, type, IP, MAC)."""

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "IP address (CIDR)", "MAC address"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        for iface in device.interfaces:
            self._append_row(
                iface.name, iface.interface_type, iface.ip_address or "",
                iface.mac_address, iface.id,
            )

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("Add interface")
        add_btn.clicked.connect(
            lambda: self._append_row("", InterfaceType.ETH_1G, "", blank_mac(), None)
        )
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        buttons_row.addWidget(add_btn)
        buttons_row.addWidget(remove_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

    # ------------------------------------------------------------ row helpers
    def _append_row(
        self,
        name: str,
        itype: InterfaceType,
        ip: str,
        mac: str,
        iface_id: str | None,
    ) -> None:
        """Add one editable row; iface_id is stashed in UserRole for reuse.

        New rows start with an all-zeros placeholder MAC
        (see domain.entities.blank_mac); the user fills in a real
        address, or leaves it as-is for a rough sketch.
        """
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.ItemDataRole.UserRole, iface_id)
        self.table.setItem(row, 0, name_item)

        combo = QComboBox()
        for choice in _TYPE_CHOICES:
            combo.addItem(choice.label, choice)
        combo.setCurrentIndex(_TYPE_CHOICES.index(itype))
        self.table.setCellWidget(row, 1, combo)

        self.table.setItem(row, 2, QTableWidgetItem(ip))
        self.table.setItem(row, 3, QTableWidgetItem(mac))

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    # ----------------------------------------------------------------- result
    def result_interfaces(self) -> list[Interface]:
        """Collect the edited rows back into Interface objects.

        Rows with an empty name are silently dropped.
        """
        result: list[Interface] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            ip_item = self.table.item(row, 2)
            mac_item = self.table.item(row, 3)
            combo = self.table.cellWidget(row, 1)

            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            itype = combo.currentData() if isinstance(combo, QComboBox) else InterfaceType.ETH_1G
            ip = (ip_item.text() if ip_item else "").strip() or None
            mac = (mac_item.text() if mac_item else "").strip() or blank_mac()
            iface_id = name_item.data(Qt.ItemDataRole.UserRole)

            if iface_id:
                result.append(Interface(
                    name=name, interface_type=itype, ip_address=ip,
                    mac_address=mac, id=iface_id,
                ))
            else:
                result.append(Interface(
                    name=name, interface_type=itype, ip_address=ip, mac_address=mac,
                ))
        return result


# Backward-compatible alias: earlier code referred to this dialog as
# InterfacesDialog before it grew a General tab.
InterfacesDialog = DevicePropertiesDialog
