"""Diagram canvas: QGraphicsScene/View with draggable device items.

Interaction model:
- With an equipment tool armed (from the palette): single-click on empty
  canvas places a device of that type with an auto-generated name.
- In select mode: drag devices to move (recorded as undoable commands),
  double-click empty space to add a device via a name prompt.
- Esc returns to select mode.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
)

from netplanner.app.controller import AppController
from netplanner.domain.entities import Device, DeviceType
from netplanner.export.styles import style_for

NODE_W, NODE_H = 120, 60


class DeviceItem(QGraphicsItem):
    def __init__(self, device: Device, controller: AppController):
        super().__init__()
        self.device = device
        self.controller = controller
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPos(device.x, device.y)

    def boundingRect(self) -> QRectF:
        return QRectF(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.boundingRect()
        style = style_for(self.device.device_type)

        painter.setBrush(QBrush(QColor(style.fill)))
        pen = QPen(QColor(style.stroke))
        pen.setWidth(3 if self.isSelected() else 1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)

        # Glyph on the left
        glyph_rect = QRectF(rect.left() + 6, rect.top(), 28, rect.height())
        glyph_font = QFont()
        glyph_font.setPointSize(16)
        painter.setFont(glyph_font)
        painter.setPen(QPen(QColor(style.stroke)))
        painter.drawText(glyph_rect, Qt.AlignmentFlag.AlignCenter, style.glyph)

        # Name + type on the right
        text_rect = QRectF(rect.left() + 36, rect.top(), rect.width() - 42, rect.height())
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(9)
        painter.setFont(name_font)
        painter.setPen(QPen(QColor("#111111")))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.device.name}\n{self.device.device_type.value.replace('_', ' ')}",
        )

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        pos = self.pos()
        if (pos.x(), pos.y()) != (self.device.x, self.device.y):
            self.controller.move_device(self.device.id, pos.x(), pos.y())
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.update_links()


class PlanScene(QGraphicsScene):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.armed_type: DeviceType | None = None
        self._device_items: dict[str, DeviceItem] = {}
        self._link_lines = []

    # -------------------------------------------------------------- rebuild
    def rebuild(self) -> None:
        self.clear()
        self._device_items.clear()
        self._link_lines.clear()
        for device in self.controller.plan.devices:
            item = DeviceItem(device, self.controller)
            self.addItem(item)
            self._device_items[device.id] = item
        self.update_links()

    def update_links(self) -> None:
        for line in self._link_lines:
            self.removeItem(line)
        self._link_lines.clear()
        pen = QPen(QColor("#555555"))
        pen.setWidth(2)
        for link in self.controller.plan.links:
            a = self._device_items.get(link.a_device_id)
            b = self._device_items.get(link.b_device_id)
            if a and b:
                line = self.addLine(
                    a.pos().x(), a.pos().y(), b.pos().x(), b.pos().y(), pen
                )
                line.setZValue(-1)
                self._link_lines.append(line)

    # ----------------------------------------------------------- placement
    def mousePressEvent(self, event) -> None:
        if (
            self.armed_type is not None
            and event.button() == Qt.MouseButton.LeftButton
            and self.itemAt(event.scenePos(), self.views()[0].transform()) is None
        ):
            self._place_armed_device(event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)

    def _place_armed_device(self, pos: QPointF) -> None:
        name = self.controller.next_device_name(self.armed_type)
        self.controller.add_device(name, self.armed_type, pos.x(), pos.y())
        self.rebuild()

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            self.armed_type is None
            and self.itemAt(event.scenePos(), self.views()[0].transform()) is None
        ):
            self._add_device_with_prompt(event.scenePos())
        else:
            super().mouseDoubleClickEvent(event)

    def _add_device_with_prompt(self, pos: QPointF) -> None:
        name, ok = QInputDialog.getText(None, "New device", "Device name:")
        if ok and name:
            self.controller.add_device(name, DeviceType.OTHER, pos.x(), pos.y())
            self.rebuild()


class NetworkCanvas(QGraphicsView):
    def __init__(self, controller: AppController, parent=None):
        self._scene = PlanScene(controller)
        super().__init__(self._scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.refresh()

    def refresh(self) -> None:
        self._scene.rebuild()

    def set_tool(self, device_type: DeviceType | None) -> None:
        self._scene.armed_type = device_type
        cursor = (
            Qt.CursorShape.CrossCursor
            if device_type is not None
            else Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            window = self.window()
            palette = getattr(window, "palette_dock", None)
            if palette is not None:
                palette.reset_to_select()
            else:
                self.set_tool(None)
            return
        super().keyPressEvent(event)
