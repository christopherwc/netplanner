"""Equipment & connections palette (Packet Tracer style).

Two sections:
- Equipment: pick a device type, then click the canvas to place it.
- Connections: pick a media type, then click two devices to link them.

Buttons are checkable and mutually exclusive across both sections.
Esc (or the Select button) returns to normal selection mode.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from netplanner.domain.entities import DeviceType, LinkType
from netplanner.export.styles import link_style_for, style_for

PALETTE_DEVICE_TYPES = [
    DeviceType.ROUTER,
    DeviceType.SWITCH,
    DeviceType.FIREWALL,
    DeviceType.SERVER,
    DeviceType.ACCESS_POINT,
    DeviceType.DISH_RADIO,
    DeviceType.AP_RADIO,
    DeviceType.WORKSTATION,
    DeviceType.OTHER,
]

PALETTE_LINK_TYPES = [
    LinkType.ETHERNET,
    LinkType.FIBER,
    LinkType.WIRELESS,
    LinkType.SERIAL,
    LinkType.WAN,
]


class EquipmentPalette(QDockWidget):
    # Emits DeviceType (place mode), LinkType (connect mode) or None (select)
    tool_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__("Palette", parent)
        body = QWidget()
        layout = QVBoxLayout(body)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._select_btn = self._make_button("Select / Move", None, layout)
        self._select_btn.setChecked(True)

        layout.addWidget(_section_label("Equipment"))
        for dtype in PALETTE_DEVICE_TYPES:
            style = style_for(dtype)
            label = f"{style.glyph}  {dtype.value.replace('_', ' ').title()}"
            self._make_button(label, dtype, layout)

        layout.addWidget(_section_label("Connections"))
        for ltype in PALETTE_LINK_TYPES:
            lstyle = link_style_for(ltype)
            btn = self._make_button(f"— {lstyle.label}", ltype, layout)
            btn.setStyleSheet(
                f"text-align: left; padding: 6px; color: {lstyle.color}; font-weight: bold;"
            )

        layout.addStretch()
        self.setWidget(body)

    def _make_button(self, text: str, tool, layout) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setStyleSheet("text-align: left; padding: 6px;")
        btn.clicked.connect(lambda: self.tool_changed.emit(tool))
        self._group.addButton(btn)
        layout.addWidget(btn)
        return btn

    def reset_to_select(self) -> None:
        self._select_btn.setChecked(True)
        self.tool_changed.emit(None)

    @staticmethod
    def preferred_area() -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.LeftDockWidgetArea


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: bold; color: #666; margin-top: 8px;")
    return label
