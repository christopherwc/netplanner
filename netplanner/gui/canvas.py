"""Diagram canvas: QGraphicsScene/View with draggable device items.

Double-click empty space to add a device. Drag devices to move them
(recorded as undoable commands on release).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
)

from netplanner.app.controller import AppController
from netplanner.domain.entities import Device, DeviceType

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
        painter.setBrush(QBrush(QColor("#e8f0fe")))
        pen = QPen(QColor("#1a56db"))
        pen.setWidth(2 if self.isSelected() else 1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QPen(QColor("#111111")))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                         f"{self.device.name}\n{self.device.device_type.value}")

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
        self._device_items: dict[str, DeviceItem] = {}
        self._link_lines = []

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

    def mouseDoubleClickEvent(self, event) -> None:
        if self.itemAt(event.scenePos(), self.views()[0].transform()) is None:
            self._add_device_at(event.scenePos())
        else:
            super().mouseDoubleClickEvent(event)

    def _add_device_at(self, pos: QPointF) -> None:
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
