"""Diagram canvas: QGraphicsScene/View with draggable device items.

Interaction model:
- Equipment tool armed: single-click empty canvas places that device
  type with an auto-generated name.
- Connection tool armed: click a first device (highlighted), then a
  second device to create a link of the armed media type. Clicking
  empty space cancels the pending first pick.
- Select mode: drag devices to move (undoable); double-click a device
  to rename it; double-click empty space to add a device via prompt.
- Esc returns to select mode.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QMessageBox,
)

from netplanner.app.controller import AppController
from netplanner.domain.entities import Device, DeviceType, LinkType
from netplanner.export.geometry import offset_endpoints, parallel_link_offsets, point_along
from netplanner.export.styles import link_style_for, style_for
from netplanner.gui.dialogs import InterfacesDialog

NODE_W, NODE_H = 120, 60
_CANCELLED = object()  # sentinel: user dismissed the interface picker


class DeviceItem(QGraphicsItem):
    def __init__(self, device: Device, controller: AppController):
        super().__init__()
        self.device = device
        self.controller = controller
        self.pending_source = False  # highlighted as first pick in connect mode
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPos(device.x, device.y)

    def boundingRect(self) -> QRectF:
        return QRectF(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.boundingRect()
        style = style_for(self.device.device_type)

        painter.setBrush(QBrush(QColor(style.fill)))
        pen = QPen(QColor("#e8710a" if self.pending_source else style.stroke))
        pen.setWidth(3 if (self.isSelected() or self.pending_source) else 1)
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

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        rename_action = menu.addAction("Rename…")
        ifaces_action = menu.addAction("Edit interfaces…")
        chosen = menu.exec(event.screenPos())
        if chosen is rename_action:
            self._rename()
        elif chosen is ifaces_action:
            dialog = InterfacesDialog(self.device)
            if dialog.exec():
                self.controller.edit_interfaces(self.device.id, dialog.result_interfaces())
                scene = self.scene()
                if isinstance(scene, PlanScene):
                    scene.update_links()
        event.accept()

    def _rename(self) -> None:
        name, ok = QInputDialog.getText(
            None, "Rename device", "Device name:", text=self.device.name
        )
        if ok and name and name != self.device.name:
            self.controller.rename_device(self.device.id, name)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        scene = self.scene()
        if isinstance(scene, PlanScene) and scene.armed_tool is None:
            self._rename()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PlanScene(QGraphicsScene):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.armed_tool: DeviceType | LinkType | None = None
        self._pending_source: DeviceItem | None = None
        self._pending_a_iface: str | None = None
        self._device_items: dict[str, DeviceItem] = {}
        self._link_items = []

    # -------------------------------------------------------------- rebuild
    def rebuild(self) -> None:
        self.clear()
        self._device_items.clear()
        self._link_items.clear()
        self._pending_source = None
        for device in self.controller.plan.devices:
            item = DeviceItem(device, self.controller)
            self.addItem(item)
            self._device_items[device.id] = item
        self.update_links()

    def update_links(self) -> None:
        for item in self._link_items:
            self.removeItem(item)
        self._link_items.clear()
        offsets = parallel_link_offsets(self.controller.plan.links)
        port_font = QFont()
        port_font.setPointSize(7)
        for link in self.controller.plan.links:
            a = self._device_items.get(link.a_device_id)
            b = self._device_items.get(link.b_device_id)
            if not (a and b):
                continue
            x1, y1, x2, y2 = offset_endpoints(
                a.pos().x(), a.pos().y(), b.pos().x(), b.pos().y(),
                offsets.get(link.id, 0.0),
            )
            lstyle = link_style_for(link.link_type)
            pen = QPen(QColor(lstyle.color))
            pen.setWidthF(lstyle.width)
            if lstyle.dash:
                pen.setDashPattern([v / lstyle.width for v in lstyle.dash])
            line = self.addLine(x1, y1, x2, y2, pen)
            line.setZValue(-1)
            self._link_items.append(line)
            if link.label:
                text = self.addSimpleText(link.label)
                text.setBrush(QBrush(QColor(lstyle.color)))
                text.setPos((x1 + x2) / 2, (y1 + y2) / 2)
                text.setZValue(-0.5)
                self._link_items.append(text)
            # Port labels near each end
            for iface_id, dev_id, t in (
                (link.a_interface_id, link.a_device_id, 0.25),
                (link.b_interface_id, link.b_device_id, 0.75),
            ):
                port = self.controller.interface_name(dev_id, iface_id)
                if port:
                    px, py = point_along(x1, y1, x2, y2, t)
                    ptext = self.addSimpleText(port)
                    ptext.setFont(port_font)
                    ptext.setBrush(QBrush(QColor("#666666")))
                    ptext.setPos(px, py)
                    ptext.setZValue(-0.5)
                    self._link_items.append(ptext)

    # ------------------------------------------------------------ mouse flow
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        device_item = item if isinstance(item, DeviceItem) else None

        if isinstance(self.armed_tool, DeviceType) and device_item is None:
            self._place_armed_device(event.scenePos())
            event.accept()
            return

        if isinstance(self.armed_tool, LinkType):
            self._handle_connect_click(device_item)
            event.accept()
            return

        super().mousePressEvent(event)

    def _place_armed_device(self, pos: QPointF) -> None:
        name = self.controller.next_device_name(self.armed_tool)
        self.controller.add_device(name, self.armed_tool, pos.x(), pos.y())
        self.rebuild()

    def _handle_connect_click(self, device_item: DeviceItem | None) -> None:
        if device_item is None:
            self._clear_pending()
            return
        if self._pending_source is None:
            iface_id = self._pick_interface(device_item)
            if iface_id is _CANCELLED:
                return
            self._pending_source = device_item
            self._pending_a_iface = iface_id
            device_item.pending_source = True
            device_item.update()
            return
        if device_item is self._pending_source:
            self._clear_pending()
            return
        b_iface_id = self._pick_interface(device_item)
        if b_iface_id is _CANCELLED:
            return
        self.controller.add_link(
            self._pending_source.device.id,
            device_item.device.id,
            link_type=self.armed_tool,
            a_interface_id=self._pending_a_iface,
            b_interface_id=b_iface_id,
        )
        self._clear_pending()
        self.update_links()

    def _pick_interface(self, device_item: DeviceItem):
        """Popup of free interfaces; returns id, None (no ports defined) or _CANCELLED."""
        device = device_item.device
        free = self.controller.free_interfaces(device.id)
        if not device.interfaces:
            return None  # device has no port list; allow untyped connection
        if not free:
            QMessageBox.warning(
                None,
                "No free interfaces",
                f"All interfaces on '{device.name}' are in use.\n"
                "Free one up or add more via right-click → Edit interfaces.",
            )
            return _CANCELLED
        menu = QMenu()
        actions = {menu.addAction(i.name): i.id for i in free}
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return _CANCELLED
        return actions[chosen]

    def _clear_pending(self) -> None:
        if self._pending_source is not None:
            self._pending_source.pending_source = False
            self._pending_source.update()
            self._pending_source = None
        self._pending_a_iface = None

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        if self.armed_tool is None and item is None:
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

    def set_tool(self, tool: DeviceType | LinkType | None) -> None:
        self._scene.armed_tool = tool
        self._scene._clear_pending()
        cursor = (
            Qt.CursorShape.CrossCursor if tool is not None else Qt.CursorShape.ArrowCursor
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
