"""Equipment palette: pick a device type, then click the canvas to place it.

The palette owns the "armed tool" state. Buttons are checkable and
mutually exclusive; pressing Esc or placing with "Select" active
returns to normal selection mode.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from netplanner.domain.entities import DeviceType
from netplanner.export.styles import style_for

PALETTE_TYPES = [
    DeviceType.ROUTER,
    DeviceType.SWITCH,
    DeviceType.FIREWALL,
    DeviceType.SERVER,
    DeviceType.ACCESS_POINT,
    DeviceType.WORKSTATION,
    DeviceType.OTHER,
]


class EquipmentPalette(QDockWidget):
    # Emitted with the armed DeviceType, or None when back in select mode
    tool_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__("Equipment", parent)
        body = QWidget()
        layout = QVBoxLayout(body)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._select_btn = self._make_button("Select / Move", None, layout)
        self._select_btn.setChecked(True)

        for dtype in PALETTE_TYPES:
            style = style_for(dtype)
            label = f"{style.glyph}  {dtype.value.replace('_', ' ').title()}"
            self._make_button(label, dtype, layout)

        layout.addStretch()
        self.setWidget(body)

    def _make_button(self, text: str, dtype: DeviceType | None, layout) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setStyleSheet("text-align: left; padding: 6px;")
        btn.clicked.connect(lambda: self.tool_changed.emit(dtype))
        self._group.addButton(btn)
        layout.addWidget(btn)
        return btn

    def reset_to_select(self) -> None:
        self._select_btn.setChecked(True)
        self.tool_changed.emit(None)

    @staticmethod
    def preferred_area() -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.LeftDockWidgetArea
