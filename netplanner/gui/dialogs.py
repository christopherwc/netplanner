"""Dialogs (interface editor, export options, plan settings)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from netplanner.domain.entities import Device, Interface, InterfaceType, random_mac

# Row order for the Type dropdown in the editor.
_TYPE_CHOICES = [
    InterfaceType.ETH_1G,
    InterfaceType.ETH_10G,
    InterfaceType.ETH_25G,
    InterfaceType.ETH_100G,
    InterfaceType.WIRELESS,
]


class InterfacesDialog(QDialog):
    """Edit a device's interfaces: name, type (speed/wireless), and IP.

    Any number of interfaces can be added or removed. Existing
    interfaces keep their ids so links referencing them stay attached;
    rows without a stored id become brand-new Interface objects.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Interfaces — {device.name}")
        self.resize(520, 360)

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
            lambda: self._append_row("", InterfaceType.ETH_1G, "", random_mac(), None)
        )
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        buttons_row.addWidget(add_btn)
        buttons_row.addWidget(remove_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

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

        New rows arrive with a pre-generated MAC so every interface
        always has one; the user can overwrite it with real hardware
        addresses when documenting an existing network.
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
            mac = (mac_item.text() if mac_item else "").strip() or random_mac()
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
