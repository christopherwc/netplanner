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

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
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
from netplanner.domain.entities import Device, DeviceType, Link, LinkType, Site, TextBox
from netplanner.export import nodecard, vlans
from netplanner.export.geometry import (
    label_anchor,
    lift_above_line,
    offset_endpoints,
    parallel_link_offsets,
)
from netplanner.export.styles import DIAGRAM_BG, link_style_for
from netplanner.gui.dialogs import (
    DevicePropertiesDialog,
    LinkPropertiesDialog,
    SiteDialog,
    TextBoxDialog,
)
from netplanner.gui.palette import SITE_TOOL, TEXT_TOOL

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
    def _vlan_filter(self) -> set[int]:
        """The scene's active VLAN filter, or empty when unattached."""
        scene = self.scene()
        return scene.vlan_filter if isinstance(scene, PlanScene) else set()

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
        # A VLAN filter dims non-members rather than hiding them, so the
        # topology stays legible while members stand out.
        fill = QColor(card.fill)
        stroke = QColor(card.stroke)
        if not card.matches_filter:
            fill.setAlphaF(0.25)
            stroke = QColor(vlans.MUTED_COLOR)
        painter.setBrush(QBrush(fill))
        pen = QPen(QColor("#e8710a") if self.pending_source else stroke)
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
        card = nodecard.build_card(self.device, self._vlan_filter())
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

        # Config attachment indicator, only when files are attached
        if card.config_line:
            config_font = QFont()
            config_font.setPointSize(7)
            config_font.setItalic(True)
            painter.setFont(config_font)
            painter.setPen(QPen(QColor("#7627bb")))
            painter.drawText(
                QRectF(left + 8, y, card.width - 16, nodecard.CONFIG_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                card.config_line,
            )
            y += nodecard.CONFIG_H

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
            # Colour chips make VLAN membership scannable without reading
            # the numbers; the text stays for exact ids.
            chip_x = left + 16
            chip_y = y + third * 2 + (third - nodecard.VLAN_CHIP_H) / 2
            for chip_color in block.vlan_colors:
                color = QColor(chip_color)
                if not block.matches_filter:
                    color = QColor(vlans.MUTED_COLOR)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawRect(
                    QRectF(chip_x, chip_y, nodecard.VLAN_CHIP_W, nodecard.VLAN_CHIP_H)
                )
                chip_x += nodecard.VLAN_CHIP_W + nodecard.VLAN_CHIP_GAP

            text_x = chip_x + 3 if block.vlan_colors else left + 16
            painter.setPen(
                QPen(QColor(vlans.MUTED_TEXT if not block.matches_filter else "#1a56db"))
            )
            painter.drawText(
                QRectF(text_x, y + third * 2, card.width - (text_x - left) - 8, third),
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
        card = nodecard.build_card(self.device, self._vlan_filter())
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
                # Configs are a separate undo step: they are bulk content
                # rather than device settings, and pushing them together
                # would make one Ctrl+Z revert both a config import and a
                # VLAN change the user made in the same sitting.
                if dialog.result_configs() != self.device.configs:
                    self.controller.edit_configs(self.device.id, dialog.result_configs())
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


class SiteItem(QGraphicsItem):
    """A resizable backdrop box marking a physical location.

    Sits behind every other item so equipment drawn on top of it reads
    as being *in* that location. Dragging the body moves the box;
    dragging the bottom-right grip resizes it. Both commit on release
    as a single undoable change.
    """

    HEADER_H = 26.0    # title band across the top
    GRIP = 16.0        # bottom-right resize handle
    MIN_W = 160.0
    MIN_H = 120.0
    NOTES_LINE_H = 12.0
    NOTES_MAX_LINES = 4

    def __init__(self, site: Site, controller: AppController):
        super().__init__()
        self.site = site
        self.controller = controller
        self.setPos(site.x, site.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        # Behind links (-1) and cards (0): a site is a backdrop.
        self.setZValue(-20)
        self._resizing = False
        self._resize_origin = QPointF()
        self._origin_size = (site.width, site.height)

    # ---------------------------------------------------------- geometry
    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.site.width, self.site.height)

    def _grip_rect(self) -> QRectF:
        return QRectF(
            self.site.width - self.GRIP, self.site.height - self.GRIP, self.GRIP, self.GRIP
        )

    def _notes_lines(self) -> list[str]:
        """Notes wrapped to the box width, capped so a long note can't
        cover the equipment drawn on top of the site."""
        if not self.site.notes:
            return []
        chars = max(12, int((self.site.width - 16) / 6))
        lines: list[str] = []
        for paragraph in self.site.notes.split("\n"):
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if len(candidate) > chars and current:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            lines.append(current)
            if len(lines) > self.NOTES_MAX_LINES:
                break
        if len(lines) > self.NOTES_MAX_LINES:
            lines = lines[: self.NOTES_MAX_LINES]
            lines[-1] = lines[-1].rstrip() + "…"
        return [line for line in lines if line]

    # ------------------------------------------------------------- paint
    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.boundingRect()
        color = QColor(self.site.color)

        # Light tint of the site colour: visible as a region without
        # competing with the device cards drawn on top.
        fill = QColor(color)
        fill.setAlphaF(0.08)
        painter.setBrush(QBrush(fill))
        pen = QPen(color)
        pen.setWidthF(2.0 if self.isSelected() else 1.5)
        if not self.isSelected():
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 8, 8)

        # Header band with the site name.
        header = QRectF(0, 0, self.site.width, self.HEADER_H)
        header_fill = QColor(color)
        header_fill.setAlphaF(0.20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(header_fill))
        painter.drawRoundedRect(header, 8, 8)
        painter.drawRect(QRectF(0, self.HEADER_H - 8, self.site.width, 8))

        title_font = QFont()
        title_font.setBold(True)
        title_font.setPixelSize(13)
        painter.setFont(title_font)
        painter.setPen(QPen(color))
        painter.drawText(
            header.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.site.name or "(unnamed site)",
        )

        # Notes under the header, in the site colour but muted.
        notes_lines = self._notes_lines()
        if notes_lines:
            notes_font = QFont()
            notes_font.setPixelSize(10)
            painter.setFont(notes_font)
            notes_color = QColor(color)
            notes_color.setAlphaF(0.85)
            painter.setPen(QPen(notes_color))
            y = self.HEADER_H + 4
            for line in notes_lines:
                painter.drawText(
                    QRectF(10, y, self.site.width - 20, self.NOTES_LINE_H),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
                y += self.NOTES_LINE_H

        # Resize grip: two short strokes in the corner, shown on hover
        # or selection so an idle diagram stays clean.
        if self.isSelected() or self._hovered_grip:
            grip = self._grip_rect()
            grip_pen = QPen(color)
            grip_pen.setWidthF(2.0)
            painter.setPen(grip_pen)
            for inset in (4, 9):
                painter.drawLine(
                    QPointF(grip.right() - inset, grip.bottom() - 2),
                    QPointF(grip.right() - 2, grip.bottom() - inset),
                )

    # ------------------------------------------------------------- mouse
    _hovered_grip = False

    def hoverMoveEvent(self, event) -> None:
        """Show the grip and a resize cursor when over the corner."""
        over = self._grip_rect().contains(event.pos())
        if over != self._hovered_grip:
            self._hovered_grip = over
            self.setCursor(
                QCursor(Qt.CursorShape.SizeFDiagCursor if over else Qt.CursorShape.ArrowCursor)
            )
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered_grip = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Start a resize when the press lands on the grip, else move."""
        if self._grip_rect().contains(event.pos()):
            self._resizing = True
            self._resize_origin = event.scenePos()
            self._origin_size = (self.site.width, self.site.height)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            self.prepareGeometryChange()
            self.site.width = max(self.MIN_W, self._origin_size[0] + delta.x())
            self.site.height = max(self.MIN_H, self._origin_size[1] + delta.y())
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Commit the move or resize as one undoable command."""
        if self._resizing:
            self._resizing = False
            # Reset to the pre-drag size so the command's snapshot of the
            # old geometry is accurate, then apply through the stack.
            width, height = self.site.width, self.site.height
            self.site.width, self.site.height = self._origin_size
            self.controller.set_site_geometry(
                self.site.id, self.site.x, self.site.y, width, height
            )
            event.accept()
            return

        super().mouseReleaseEvent(event)
        if (self.pos().x(), self.pos().y()) != (self.site.x, self.site.y):
            self.controller.set_site_geometry(
                self.site.id, self.pos().x(), self.pos().y(),
                self.site.width, self.site.height,
            )

    def mouseDoubleClickEvent(self, event) -> None:
        self._edit()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        edit_action = menu.addAction("Edit site…")
        menu.addSeparator()
        delete_action = menu.addAction("Delete site")
        chosen = menu.exec(event.screenPos())
        if chosen is edit_action:
            self._edit()
        elif chosen is delete_action:
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.delete_items([self])
        event.accept()

    def _edit(self) -> None:
        dialog = SiteDialog(
            self.site, len(self.controller.devices_in_site(self.site.id))
        )
        if dialog.exec():
            self.controller.edit_site(
                self.site.id,
                dialog.result_name(),
                dialog.result_notes(),
                dialog.result_color(),
            )
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.rebuild()


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
        """Right-click a cable: edit its label/media, or delete it."""
        menu = QMenu()
        edit_action = menu.addAction("Edit link…")
        menu.addSeparator()
        delete_action = menu.addAction("Delete link")
        chosen = menu.exec(event.screenPos())
        if chosen is edit_action:
            self._edit()
        elif chosen is delete_action:
            self.controller.delete_link(self.link)
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.rebuild()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click a cable to edit it, mirroring device behaviour."""
        self._edit()
        event.accept()

    def _edit(self) -> None:
        """Open the link dialog and commit changes as one undo step."""
        dialog = LinkPropertiesDialog(
            self.link,
            self._endpoint_summary(),
            self.controller.link_derived_speed(self.link),
        )
        if dialog.exec():
            self.controller.edit_link(
                self.link.id,
                dialog.result_label(),
                dialog.result_link_type(),
                dialog.result_bandwidth(),
                dialog.result_bandwidth_auto(),
            )
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.update_links()  # colour/label change only; cards untouched

    def _endpoint_summary(self) -> str:
        """'sw1 Gig0/1  ↔  rtr1 Gig0/0' for the dialog header."""
        plan = self.controller.plan
        a_dev = plan.get_device(self.link.a_device_id)
        b_dev = plan.get_device(self.link.b_device_id)
        a_port = self.controller.interface_name(self.link.a_device_id, self.link.a_interface_id)
        b_port = self.controller.interface_name(self.link.b_device_id, self.link.b_interface_id)
        a = f"{a_dev.name if a_dev else '?'} {a_port}".strip()
        b = f"{b_dev.name if b_dev else '?'} {b_port}".strip()
        return f"{a}  ↔  {b}"


class TextBoxItem(QGraphicsItem):
    """A draggable, selectable text annotation on the canvas.

    Drawn with a dashed border only while selected or hovered, so a
    finished diagram shows clean text rather than boxes, but the
    clickable region is still discoverable when editing.
    """

    PADDING = 4.0

    def __init__(self, textbox: TextBox, controller: AppController):
        super().__init__()
        self.textbox = textbox
        self.controller = controller
        self.setPos(textbox.x, textbox.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self._hovered = False
        # Above cards so an annotation overlapping a device stays readable.
        self.setZValue(10)

    def boundingRect(self) -> QRectF:
        """Bounds from the wrapped text, matching the export layout."""
        return QRectF(
            0, 0,
            self.textbox.width + self.PADDING * 2,
            self.textbox.height + self.PADDING * 2,
        )

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        """Draw the wrapped lines, plus a border when selected/hovered."""
        rect = self.boundingRect()

        # Annotations carry their own light panel rather than relying on
        # the canvas behind them, which follows the system theme. Without
        # it, dark annotation text is invisible on a dark desktop theme.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(DIAGRAM_BG)))
        painter.drawRoundedRect(rect, 3, 3)

        if self.isSelected() or self._hovered:
            pen = QPen(QColor("#e8710a" if self.isSelected() else "#b0b0b0"))
            pen.setWidthF(1.5 if self.isSelected() else 1.0)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRect(rect)

        font = QFont()
        # Pixel size, not point size: the exporters treat font_size as
        # canvas units, and Qt's point sizes are DPI-scaled (15pt renders
        # ~20px at 96dpi), which would make canvas text wider than the
        # wrap width computed by TextBox.display_lines.
        font.setPixelSize(max(1, round(self.textbox.font_size)))
        font.setBold(self.textbox.bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(self.textbox.color)))

        line_height = self.textbox.font_size * 1.35
        y = self.PADDING
        for line in self.textbox.display_lines:
            painter.drawText(
                QRectF(self.PADDING, y, self.textbox.width, line_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            y += line_height

    def mouseReleaseEvent(self, event) -> None:
        """Record a completed drag as an undoable move."""
        super().mouseReleaseEvent(event)
        if (self.pos().x(), self.pos().y()) != (self.textbox.x, self.textbox.y):
            self.controller.move_textbox(self.textbox.id, self.pos().x(), self.pos().y())

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click opens the editor, matching device rename behavior."""
        self._edit()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        edit_action = menu.addAction("Edit text…")
        menu.addSeparator()
        delete_action = menu.addAction("Delete text box")
        chosen = menu.exec(event.screenPos())
        if chosen is edit_action:
            self._edit()
        elif chosen is delete_action:
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.delete_items([self])
        event.accept()

    def _edit(self) -> None:
        """Open the text box dialog and commit changes as one undo step."""
        dialog = TextBoxDialog(self.textbox)
        if dialog.exec():
            self.controller.edit_textbox(
                self.textbox.id,
                dialog.result_text(),
                dialog.result_font_size(),
                dialog.result_bold(),
                dialog.result_color(),
                dialog.result_width(),
            )
            scene = self.scene()
            if isinstance(scene, PlanScene):
                scene.rebuild()  # size follows the text, so re-lay out


class PlanScene(QGraphicsScene):
    """The diagram scene: owns device items, link lines, and tool state.

    armed_tool decides what clicks do (place device / connect / select);
    show_details toggles detailed cards vs compact nodes.
    """

    # Emitted after any rebuild, i.e. whenever devices, links, VLANs or
    # annotations may have changed. Docked panels derived from the plan
    # (the VLAN legend) listen to this: canvas edits never pass through
    # the menu handlers, so without it those panels go stale the moment
    # a user places a device or edits its VLANs.
    plan_changed = pyqtSignal()

    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.armed_tool: DeviceType | LinkType | str | None = None
        self.show_details = True  # sectioned cards by default; View menu toggles
        self._pending_source: DeviceItem | None = None
        self._pending_a_iface: str | None = None
        self._device_items: dict[str, DeviceItem] = {}
        self._text_items: dict[str, TextBoxItem] = {}
        self._site_items: dict[str, SiteItem] = {}
        self._link_items = []
        # Active VLAN highlight filter; empty set = show everything normally.
        self.vlan_filter: set[int] = set()

    # -------------------------------------------------------------- rebuild
    def rebuild(self) -> None:
        """Recreate every graphics item from the plan (full refresh)."""
        self.clear()
        self._device_items.clear()
        self._text_items.clear()
        self._site_items.clear()
        self._link_items.clear()
        self._pending_source = None
        for device in self.controller.plan.devices:
            item = DeviceItem(device, self.controller)
            self.addItem(item)
            self._device_items[device.id] = item
        # Sites first so they're underneath; z-values enforce it anyway,
        # but creation order keeps the scene's item list readable.
        for site in self.controller.plan.sites.values():
            item = SiteItem(site, self.controller)
            self.addItem(item)
            self._site_items[site.id] = item

        for textbox in self.controller.plan.textboxes.values():
            item = TextBoxItem(textbox, self.controller)
            self.addItem(item)
            self._text_items[textbox.id] = item

        self.update_links()
        self.plan_changed.emit()

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
                label_rect = text.boundingRect()
                mx, my = lift_above_line(
                    (x1 + x2) / 2, (y1 + y2) / 2, x1, y1, x2, y2,
                    label_rect.height() / 2 + 3,
                )
                text.setPos(mx - label_rect.width() / 2, my - label_rect.height() / 2)
                # Above cards, like port labels.
                text.setZValue(5)
                self._link_items.append(text)
            # Port labels, anchored just outside each card rather than at
            # a fixed fraction along the line: a centre-to-centre line
            # spends its first stretch underneath the card, so 25%/75%
            # placement hides the label whenever devices sit close together.
            for iface_id, dev_id, item, (cx, cy), (tx, ty) in (
                (link.a_interface_id, link.a_device_id, a, (x1, y1), (x2, y2)),
                (link.b_interface_id, link.b_device_id, b, (x2, y2), (x1, y1)),
            ):
                port = self.controller.interface_name(dev_id, iface_id)
                if not port:
                    continue
                ptext = self.addSimpleText(port)
                ptext.setFont(port_font)
                ptext.setBrush(QBrush(QColor("#666666")))
                bounds = item.boundingRect()
                text_rect = ptext.boundingRect()
                px, py = label_anchor(
                    cx, cy, tx, ty,
                    bounds.width() / 2, bounds.height() / 2,
                    text_rect.width(), text_rect.height(),
                    lift=text_rect.height() / 2 + 2,
                )
                ptext.setPos(px - text_rect.width() / 2, py - text_rect.height() / 2)
                # Above cards (z 0) but below annotations (z 10): a
                # neighbouring card must never bury a port label.
                ptext.setZValue(5)
                self._link_items.append(ptext)

    # ------------------------------------------------------------ mouse flow
    def mousePressEvent(self, event) -> None:
        """Route left-clicks by armed tool: place, connect, or select."""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        device_item = item if isinstance(item, DeviceItem) else None

        if self.armed_tool == SITE_TOOL and device_item is None:
            self._place_site(event.scenePos())
            event.accept()
            return

        if self.armed_tool == TEXT_TOOL and device_item is None:
            self._place_textbox(event.scenePos())
            event.accept()
            return

        if isinstance(self.armed_tool, DeviceType) and device_item is None:
            self._place_armed_device(event.scenePos())
            event.accept()
            return

        if isinstance(self.armed_tool, LinkType):
            self._handle_connect_click(device_item)
            event.accept()
            return

        super().mousePressEvent(event)

    def _place_site(self, pos: QPointF) -> None:
        """Prompt for a name, then drop a site box at the click point."""
        placeholder = Site(x=pos.x(), y=pos.y())
        dialog = SiteDialog(placeholder, 0)
        if not dialog.exec():
            return
        self.controller.add_site(
            dialog.result_name(),
            pos.x(),
            pos.y(),
            notes=dialog.result_notes(),
            color=dialog.result_color(),
        )
        self.rebuild()

    def _place_textbox(self, pos: QPointF) -> None:
        """Prompt for text, then place an annotation at the click point."""
        placeholder = TextBox(x=pos.x(), y=pos.y())
        dialog = TextBoxDialog(placeholder)
        if not dialog.exec():
            return
        text = dialog.result_text()
        if not text.strip():
            return  # an empty annotation would be invisible and unclickable
        self.controller.add_textbox(
            text,
            pos.x(),
            pos.y(),
            font_size=dialog.result_font_size(),
            bold=dialog.result_bold(),
            color=dialog.result_color(),
            width=dialog.result_width(),
        )
        self.rebuild()

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
            # port_summary reflects a manual type name and rate when
            # either is set, so the picker shows the real port.
            menu.addAction(f"{i.name}  ({i.port_summary})"): i.id for i in free
        }
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return _CANCELLED
        return actions[chosen]

    def set_vlan_filter(self, vlan_ids: set[int]) -> None:
        """Highlight the given VLANs, dimming everything else.

        An empty set clears the filter. Only repaints — card geometry is
        filter-independent, so device positions never shift.
        """
        self.vlan_filter = set(vlan_ids)
        # Mirror onto the controller so File -> Export renders exactly
        # what is currently on screen.
        self.controller.set_vlan_filter(self.vlan_filter)
        for item in self._device_items.values():
            item.update()
        self.update()

    def delete_items(self, items) -> None:
        """Delete the given device/link items, confirming cascading loss.

        Deleting a device also removes its cables, so the user is warned
        when that would happen; everything goes through the command
        stack, and a multi-item selection is still a single undo step
        per item.
        """
        devices = [i for i in items if isinstance(i, DeviceItem)]
        links = [i for i in items if isinstance(i, LinkItem)]
        texts = [i for i in items if isinstance(i, TextBoxItem)]
        sites = [i for i in items if isinstance(i, SiteItem)]
        if not devices and not links and not texts and not sites:
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
        if texts:
            summary.append(f"{len(texts)} text box(es)")
        if sites:
            summary.append(f"{len(sites)} site(s)")
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
        for item in texts:
            self.controller.delete_textbox(item.textbox.id)
        # Sites last: deleting one never affects the devices drawn over
        # it, so ordering is cosmetic, but it keeps the undo stack
        # reading outermost-container-last.
        for item in sites:
            self.controller.delete_site(item.site.id)
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

    @property
    def plan_changed(self):
        """Signal emitted whenever the diagram is rebuilt from the plan."""
        return self._scene.plan_changed

    def set_vlan_filter(self, vlan_ids: set[int]) -> None:
        """Apply a VLAN highlight filter to the scene (used by the VLAN dock)."""
        self._scene.set_vlan_filter(vlan_ids)

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
