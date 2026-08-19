"""Dialogs (interface editor, export options, plan settings)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from netplanner.domain.entities import Device, Interface


class InterfacesDialog(QDialog):
    """Edit a device's interfaces: name and optional IP (CIDR).

    Existing interfaces keep their ids (so links referencing them stay
    valid); new rows create fresh Interface objects.
    """

    def __init__(self, device: Device, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Interfaces — {device.name}")
        self.resize(420, 320)

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "IP address (CIDR)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        for iface in device.interfaces:
            self._append_row(iface.name, iface.ip_address or "", iface.id)

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("Add interface")
        add_btn.clicked.connect(lambda: self._append_row("", "", None))
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

    def _append_row(self, name: str, ip: str, iface_id: str | None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(0x0100, iface_id)  # Qt.UserRole: keep existing id
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QTableWidgetItem(ip))

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def result_interfaces(self) -> list[Interface]:
        result: list[Interface] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            ip_item = self.table.item(row, 1)
            name = (name_item.text() if name_item else "").strip()
            if not name:
                continue
            ip = (ip_item.text() if ip_item else "").strip() or None
            iface_id = name_item.data(0x0100)
            if iface_id:
                result.append(Interface(name=name, ip_address=ip, id=iface_id))
            else:
                result.append(Interface(name=name, ip_address=ip))
        return result
