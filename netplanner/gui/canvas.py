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
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QMessageBox,
)

from netplanner.app.controller import AppController
from netplanner.domain.entities import Device, DeviceType, Link, LinkType
from netplanner.export.geometry import offset_endpoints, parallel_link_offsets, point_along
from netplanner.export import nodecard
from netplanner.export.styles import link_style_for, style_for
from netplanner.gui.dialogs import DevicePropertiesDialog

# Compact node size used when View -> "Show device details" is off.
# Detailed-card metrics come from export.nodecard so the GUI and the
# PDF/PNG exporters always agree on node geometry.
NODE_W, NODE_H = 120, 60
_CANCELLED = object()  # sentinel: user dismissed the interface picker


class DeviceItem(QGraphicsItem):
    """A device rendered as a sectioned card (or a compact node).

    Detailed card layout (default):
        [glyph] name          <- header
        Type: router          <- device-type section
        ----------------------
        Gig0/0 - 10.0.0.1/24  <- one block per interface
          02:AB:CD:12:34:56      (MAC on its own line, monospace-ish)

    When the scene's show_details flag is off, the old compact
    glyph+name node is drawn instead. The bounding rect is computed
    from the content, so cards grow with their interface count.
    """

    def __init__(self, device: Device, controller: AppController):
        super().__init__()
        self.device = device
        self.controller = controller
        self.pending_source = False  # highlighted as first pick in connect mode
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPos(device.x, device.y)

    # ------------------------------------------------------------- geometry
    def _details_on(self) -> bool:
        """Whether the scene is showing detailed cards vs compact nodes."""
        scene = self.scene()
        return bool(getattr(scene, "show_details", True))

    def boundingRect(self) -> QRectF:
        """Item bounds: sized from the card layout, or the compact node."""
        if self._details_on():
            card = nodecard.build_card(self.device)
            return QRectF(-card.width / 2, -card.height / 2, card.width, card.height)
        return QRectF(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H)

    # -------------------------------------------------------------- painting
    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Qt paint hook: dispatch to the detailed card or compact node."""
        if self._details_on():
            self._paint_card(painter)
        else:
            self._paint_compact(painter)

    def _frame(self, painter: QPainter, rect: QRectF, card: nodecard.NodeCard) -> None:
        """Draw the shared rounded frame with selection/pending highlight.

        Uses the card's already-status-adjusted fill/stroke (grayed out
        for BROKEN devices) rather than the raw device-type colors.
        """
        painter.setBrush(QBrush(QColor(card.fill)))
        pen = QPen(QColor("#e8710a" if self.pending_source else card.stroke))
        pen.setWidth(3 if (self.isSelected() or self.pending_source) else 1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)

        if card.striped:
            self._paint_status_stripes(painter, rect, card.stripe_colors)

    def _paint_status_stripes(
        self, painter: QPainter, rect: QRectF, colors: list[str]
    ) -> None:
        """Overlay diagonal stripes across the card for its status tag.

        Clips to the card's rounded-rect shape so stripes never spill
        past the border, then draws parallel diagonal lines spanning
        the card at a fixed spacing, cycling through `colors` per line:
        PLANNED passes a single gray so every stripe matches, BROKEN
        passes [red, black] so the stripes alternate hazard-tape style.
        """
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(rect, 6, 6)
        painter.setClipPath(clip_path)

        # Diagonal lines at 45 degrees, spaced STRIPE_SPACING apart,
        # spanning well past the card's diagonal so corners are covered.
        span = rect.width() + rect.height()
        step = nodecard.STRIPE_SPACING
        offset = -span
        line_index = 0
        while offset < span:
            color = QColor(colors[line_index % len(colors)])
            color.setAlphaF(nodecard.STRIPE_ALPHA)  # keep card text readable
            pen = QPen(color)
            pen.setWidthF(nodecard.STRIPE_WIDTH)
            painter.setPen(pen)
            x1, y1 = rect.left() + offset, rect.top()
            x2, y2 = rect.left() + offset + rect.height(), rect.bottom()
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            offset += step
            line_index += 1

        painter.restore()

    def _paint_card(self, painter: QPainter) -> None:
        """Detailed card: header / colored type band / interface IP+MAC blocks.

        Layout and sizing come from export.nodecard, so this rendering is
        pixel-compatible with the PDF/PNG exporters.
        """
        card = nodecard.build_card(self.device)
        rect = self.boundingRect()
        self._frame(painter, rect, card)

        left = rect.left()
        top = rect.top()

        # Header: glyph + bold device name
        glyph_font = QFont()
        glyph_font.setPointSize(12)
        painter.setFont(glyph_font)
        painter.setPen(QPen(QColor(card.stroke)))
        painter.drawText(
            QRectF(left + 6, top, 22, nodecard.HEADER_H),
            Qt.AlignmentFlag.AlignCenter,
            card.glyph,
        )
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(10)
        painter.setFont(name_font)
        painter.setPen(QPen(QColor("#111111")))
        painter.drawText(
            QRectF(left + 30, top, card.width - 36, nodecard.HEADER_H),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            card.name,
        )

        y = top + nodecard.HEADER_H

        # Device model: small line under the name, only when set
        if card.device_model:
            model_font = QFont()
            model_font.setPointSize(7)
            model_font.setItalic(True)
            painter.setFont(model_font)
            painter.setPen(QPen(QColor("#555555")))
            painter.drawText(
                QRectF(left + 8, y, card.width - 16, nodecard.MODEL_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                card.device_model,
            )
            y += nodecard.MODEL_H

        # Device-type band: filled strip in the type's color
        band_rect = QRectF(left, y, card.width, nodecard.TYPE_BAND_H)
        painter.setBrush(QBrush(QColor(card.stroke)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(band_rect)
        band_font = QFont()
        band_font.setPointSize(7)
        band_font.setBold(True)
        painter.setFont(band_font)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(band_rect, Qt.AlignmentFlag.AlignCenter, card.type_label.upper())
        y += nodecard.TYPE_BAND_H

        # Native VLAN: always shown (device-wide default is VLAN 1)
        native_vlan_font = QFont()
        native_vlan_font.setPointSize(7)
        native_vlan_font.setBold(True)
        painter.setFont(native_vlan_font)
        painter.setPen(QPen(QColor("#333333")))
        painter.drawText(
            QRectF(left + 8, y, card.width - 16, nodecard.NATIVE_VLAN_H),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            card.native_vlan_line,
        )
        y += nodecard.NATIVE_VLAN_H

        # Loopback IP: single line, only when set
        if card.loopback_line:
            loopback_font = QFont()
            loopback_font.setPointSize(7)
            loopback_font.setBold(True)
            painter.setFont(loopback_font)
            painter.setPen(QPen(QColor("#333333")))
            painter.drawText(
                QRectF(left + 8, y, card.width - 16, nodecard.LOOPBACK_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                card.loopback_line,
            )
            y += nodecard.LOOPBACK_H

        # Interface blocks: "name  ip" line, MAC beneath in gray, VLAN beneath that
        iface_font = QFont()
        iface_font.setPointSize(8)
        mac_font = QFont()
        mac_font.setPointSize(7)
        vlan_font = QFont()
        vlan_font.setPointSize(7)
        third = nodecard.IFACE_BLOCK_H / 3
        for block in card.iface_blocks:
            painter.setFont(iface_font)
            painter.setPen(QPen(QColor("#111111")))
            painter.drawText(
                QRectF(left + 8, y, card.width - 16, third + 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                block.top,
            )
            painter.setFont(mac_font)
            painter.setPen(QPen(QColor("#777777")))
            painter.drawText(
                QRectF(left + 16, y + third, card.width - 24, third),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                block.mac,
            )
            painter.setFont(vlan_font)
            painter.setPen(QPen(QColor("#1a56db")))
            painter.drawText(
                QRectF(left + 16, y + third * 2, card.width - 24, third),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                block.vlan,
            )
            y += nodecard.IFACE_BLOCK_H

        # Overflow indicator for devices with many ports
        if card.more_count:
            painter.setFont(mac_font)
            painter.setPen(QPen(QColor("#555555")))
            painter.drawText(
                QRectF(left + 8, y, card.width - 16, nodecard.FOOTER_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"+{card.more_count} more…",
            )
            y += nodecard.FOOTER_H

        # Notes: wrapped, word-limited lines, only when set
        if card.notes_lines:
            y += nodecard.PAD / 2
            painter.setPen(QPen(QColor(card.stroke)))
            painter.drawLine(int(left + 4), int(y), int(left + card.width - 4), int(y))
            y += 2
            notes_font = QFont()
            notes_font.setPointSize(7)
            notes_font.setItalic(True)
            painter.setFont(notes_font)
            painter.setPen(QPen(QColor("#444444")))
            for line in card.notes_lines:
                painter.drawText(
                    QRectF(left + 8, y, card.width - 16, nodecard.NOTES_LINE_H),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
                y += nodecard.NOTES_LINE_H

    def _paint_compact(self, painter: QPainter) -> None:
        """Small glyph+name node used when device details are hidden.

        Still built from the card so status stripes and colors apply
        in compact mode too.
        """
        rect = self.boundingRect()
        card = nodecard.build_card(self.device)
        self._frame(painter, rect, card)

        glyph_rect = QRectF(rect.left() + 6, rect.top(), 28, rect.height())
        glyph_font = QFont()
        glyph_font.setPointSize(16)
        painter.setFont(glyph_font)
        painter.setPen(QPen(QColor(card.stroke)))
        painter.drawText(glyph_rect, Qt.AlignmentFlag.AlignCenter, card.glyph)

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
        """End of a drag: record the move as an undoable command."""
        super().mouseReleaseEvent(event)
        pos = self.pos()
        if (pos.x(), pos.y()) != (self.device.x, self.device.y):
            self.controller.move_device(self.device.id, pos.x(), pos.y())
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.update_links()

    def contextMenuEvent(self, event) -> None:
        """Right-click menu: rename or open the properties dialog."""
        menu = QMenu()
        rename_action = menu.addAction("Rename…")
        props_action = menu.addAction("Edit properties…")
        menu.addSeparator()
        delete_action = menu.addAction("Delete device")
        chosen = menu.exec(event.screenPos())
        if chosen is delete_action:
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.delete_items([self])
        elif chosen is rename_action:
            self._rename()
        elif chosen is props_action:
            dialog = DevicePropertiesDialog(self.device)
            if dialog.exec():
                self.controller.edit_device_properties(
                    self.device.id,
                    dialog.result_device_model(),
                    dialog.result_loopback_ip(),
                    dialog.result_notes(),
                    dialog.result_native_vlan(),
                    dialog.result_status(),
                    dialog.result_interfaces(),
                )
                scene = self.scene()
                if isinstance(scene, PlanScene):
                    scene.rebuild()  # card height depends on all edited fields
        event.accept()

    def _rename(self) -> None:
        """Prompt for a new device name and apply it (undoable)."""
        name, ok = QInputDialog.getText(
            None, "Rename device", "Device name:", text=self.device.name
        )
        if ok and name and name != self.device.name:
            self.controller.rename_device(self.device.id, name)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click a device in select mode: rename it."""
        scene = self.scene()
        if isinstance(scene, PlanScene) and scene.armed_tool is None:
            self._rename()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class LinkItem(QGraphicsLineItem):
    """A selectable cable between two devices.

    Plain QGraphicsLineItem lines are hard to hit with a mouse, so this
    widens the clickable area via a shape() stroked well beyond the
    drawn pen width, and highlights on selection so the user can see
    what they are about to delete.
    """

    HIT_WIDTH = 12.0  # generous click target regardless of drawn width

    def __init__(self, link: Link, controller: AppController, x1, y1, x2, y2, pen: QPen):
        super().__init__(x1, y1, x2, y2)
        self.link = link
        self.controller = controller
        self._base_pen = pen
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1)

    def shape(self):
        """Widen the hit area so thin/dashed cables are still clickable."""
        stroker = QPainterPathStroker()
        stroker.setWidth(self.HIT_WIDTH)
        path = QPainterPath()
        path.moveTo(self.line().p1())
        path.lineTo(self.line().p2())
        return stroker.createStroke(path)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Draw the cable, thickened and highlighted while selected."""
        if self.isSelected():
            pen = QPen(self._base_pen)
            pen.setWidthF(self._base_pen.widthF() + 2)
            pen.setColor(QColor("#e8710a"))
            painter.setPen(pen)
            painter.drawLine(self.line())
        else:
            painter.setPen(self._base_pen)
            painter.drawLine(self.line())

    def contextMenuEvent(self, event) -> None:
        """Right-click a cable: delete it."""
        menu = QMenu()
        delete_action = menu.addAction("Delete link")
        if menu.exec(event.screenPos()) is delete_action:
            self.controller.delete_link(self.link)
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.rebuild()
        event.accept()


class PlanScene(QGraphicsScene):
    """The diagram scene: owns device items, link lines, and tool state.

    armed_tool decides what clicks do (place device / connect / select);
    show_details toggles detailed cards vs compact nodes.
    """

    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.armed_tool: DeviceType | LinkType | None = None
        self.show_details = True  # sectioned cards by default; View menu toggles
        self._pending_source: DeviceItem | None = None
        self._pending_a_iface: str | None = None
        self._device_items: dict[str, DeviceItem] = {}
        self._link_items = []

    # -------------------------------------------------------------- rebuild
    def rebuild(self) -> None:
        """Recreate every graphics item from the plan (full refresh)."""
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
        """Redraw links only (cheaper than rebuild; used while dragging).

        Parallel links between the same device pair are fanned out with
        perpendicular offsets so they never overlap; interface names are
        drawn as small port labels near each end.
        """
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
            line = LinkItem(link, self.controller, x1, y1, x2, y2, pen)
            self.addItem(line)
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
        """Route left-clicks by armed tool: place, connect, or select."""
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
        """Equipment tool click: drop an auto-named device at pos."""
        name = self.controller.next_device_name(self.armed_tool)
        self.controller.add_device(name, self.armed_tool, pos.x(), pos.y())
        self.rebuild()

    def _handle_connect_click(self, device_item: DeviceItem | None) -> None:
        """Connection tool click: first pick a source, then a target.

        Each pick pops up the device's free-interface menu; the link is
        created once both endpoints have a chosen port.
        """
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
        actions = {
            menu.addAction(f"{i.name}  ({i.interface_type.label})"): i.id for i in free
        }
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return _CANCELLED
        return actions[chosen]

    def delete_items(self, items) -> None:
        """Delete the given device/link items, confirming cascading loss.

        Deleting a device also removes its cables, so the user is warned
        when that would happen; everything goes through the command
        stack, and a multi-item selection is still a single undo step
        per item.
        """
        devices = [i for i in items if isinstance(i, DeviceItem)]
        links = [i for i in items if isinstance(i, LinkItem)]
        if not devices and not links:
            return

        # Count cables that would disappear as a side effect of removing
        # devices, ignoring ones the user already selected explicitly.
        selected_link_ids = {i.link.id for i in links}
        cascading = {
            link.id
            for item in devices
            for link in self.controller.links_for_device(item.device.id)
            if link.id not in selected_link_ids
        }

        summary = []
        if devices:
            summary.append(
                f"{len(devices)} device(s): "
                + ", ".join(i.device.name for i in devices)
            )
        if links:
            summary.append(f"{len(links)} link(s)")
        message = "Delete " + " and ".join(summary) + "?"
        if cascading:
            message += (
                f"\n\nThis will also remove {len(cascading)} attached "
                "link(s). This can be undone with Ctrl+Z."
            )

        confirm = QMessageBox.question(
            None,
            "Confirm delete",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm is not QMessageBox.StandardButton.Yes:
            return

        # Links first: deleting a device would otherwise take them with
        # it, leaving a redundant no-op command on the undo stack.
        for item in links:
            self.controller.delete_link(item.link)
        for item in devices:
            self.controller.delete_device(item.device.id)
        self.rebuild()

    def delete_selection(self) -> None:
        """Delete whatever is currently selected on the canvas."""
        self.delete_items(list(self.selectedItems()))

    def keyPressEvent(self, event) -> None:
        """Delete/Backspace removes the current selection."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _clear_pending(self) -> None:
        """Cancel a half-made connection (first device already picked)."""
        if self._pending_source is not None:
            self._pending_source.pending_source = False
            self._pending_source.update()
            self._pending_source = None
        self._pending_a_iface = None

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click empty canvas in select mode: add a device."""
        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        if self.armed_tool is None and item is None:
            self._add_device_with_prompt(event.scenePos())
        else:
            super().mouseDoubleClickEvent(event)

    def _add_device_with_prompt(self, pos: QPointF) -> None:
        """Double-click on empty canvas: name a new generic device."""
        name, ok = QInputDialog.getText(None, "New device", "Device name:")
        if ok and name:
            self.controller.add_device(name, DeviceType.OTHER, pos.x(), pos.y())
            self.rebuild()


class NetworkCanvas(QGraphicsView):
    """The scrollable, antialiased view wrapping PlanScene.

    Exposes the small API the main window uses: refresh(), set_tool(),
    and set_show_details(); Esc resets the palette to select mode.
    """

    def __init__(self, controller: AppController, parent=None):
        self._scene = PlanScene(controller)
        super().__init__(self._scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the scene after external changes (load, undo, layout...)."""
        self._scene.rebuild()

    def set_show_details(self, on: bool) -> None:
        """Toggle sectioned cards (IPs, MACs, type) vs compact nodes."""
        self._scene.show_details = on
        self._scene.rebuild()

    def delete_selection(self) -> None:
        """Delete the canvas selection (used by the Edit menu action)."""
        self._scene.delete_selection()

    def set_tool(self, tool: DeviceType | LinkType | None) -> None:
        """Arm a palette tool: DeviceType places, LinkType connects, None selects."""
        self._scene.armed_tool = tool
        self._scene._clear_pending()
        cursor = (
            Qt.CursorShape.CrossCursor if tool is not None else Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)

    def keyPressEvent(self, event) -> None:
        """Esc returns to Select/Move mode via the palette."""
        if event.key() == Qt.Key.Key_Escape:
            window = self.window()
            palette = getattr(window, "palette_dock", None)
            if palette is not None:
                palette.reset_to_select()
            else:
                self.set_tool(None)
            return
        super().keyPressEvent(event)
