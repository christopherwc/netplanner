"""Export a NetworkPlan to PDF using reportlab.

reportlab's coordinate origin is bottom-left, while the scene uses
top-left; fy() flips the axis. Nodes are drawn as the same three-section
cards the GUI shows (header / type band / interface IP+MAC blocks).
"""

from __future__ import annotations

import logging

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas as pdf_canvas

from netplanner.domain.model import NetworkPlan
from netplanner.errors import ExportError

from .geometry import point_along
from .nodecard import (
    FOOTER_H,
    HEADER_H,
    IFACE_BLOCK_H,
    LOOPBACK_H,
    MODEL_H,
    CONFIG_H,
    NATIVE_VLAN_H,
    NOTES_LINE_H,
    PAD,
    STRIPE_ALPHA,
    STRIPE_SPACING,
    STRIPE_WIDTH,
    TYPE_BAND_H,
)
from .renderer import Scene, build_scene
from .nodecard import VLAN_CHIP_GAP, VLAN_CHIP_H, VLAN_CHIP_W
from .styles import DIAGRAM_BG, link_style_for_value
from .vlans import MUTED_COLOR, MUTED_TEXT

TEXT_COLOR = HexColor("#111111")
MAC_COLOR = HexColor("#777777")
TITLE_OFFSET = 40  # room reserved above the diagram for the plan title


logger = logging.getLogger(__name__)


def export_pdf(plan: NetworkPlan, path: Path, vlan_filter: set[int] | None = None) -> None:
    """Render the plan to a single-page PDF sized to fit the diagram.

    Failures are logged with the traceback and re-raised as ExportError
    naming the plan, the destination, and the scene size — enough to
    tell an unwritable path from a rendering bug from the message alone.
    """
    scene = build_scene(plan, vlan_filter)
    logger.info(
        "Exporting plan '%s' (%d devices, %d links) to PDF %s (%.0fx%.0f pts)",
        plan.name, len(plan.devices), len(plan.links), path, scene.width, scene.height,
    )
    try:
        _export_pdf_impl(scene, path)
    except OSError as exc:
        logger.exception("PDF export failed writing %s", path)
        raise ExportError(
            f"Could not write PDF for plan '{plan.name}' to {path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    except Exception as exc:  # rendering bug: keep the report verbose
        logger.exception("PDF rendering failed for plan '%s'", plan.name)
        raise ExportError(
            f"PDF rendering failed for plan '{plan.name}' "
            f"({len(plan.devices)} devices, scene {scene.width:.0f}x{scene.height:.0f}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    logger.debug("PDF export complete: %s", path)


def _export_pdf_impl(scene, path: Path) -> None:
    """The actual reportlab drawing behind export_pdf()."""
    c = pdf_canvas.Canvas(str(path), pagesize=(scene.width, scene.height + TITLE_OFFSET))
    _draw(c, scene)
    c.showPage()
    c.save()


def _draw(c: pdf_canvas.Canvas, scene: Scene) -> None:
    """Paint the whole scene: title, edges, cards, then text annotations."""
    page_h = scene.height + TITLE_OFFSET

    def fy(y: float) -> float:
        """Flip y: scene uses top-left origin, PDF uses bottom-left."""
        return page_h - y - TITLE_OFFSET

    # Plan title
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20, page_h - 25, scene.title)

    # Edges first so they render under the node cards
    for e in scene.edges:
        lstyle = link_style_for_value(e.link_type)
        c.setStrokeColor(HexColor(lstyle.color))
        c.setLineWidth(lstyle.width)
        c.setDash(list(lstyle.dash) if lstyle.dash else [])
        c.line(e.x1, fy(e.y1), e.x2, fy(e.y2))
        if e.label:
            c.setFillColor(HexColor(lstyle.color))
            c.setFont("Helvetica", 7)
            c.drawCentredString((e.x1 + e.x2) / 2, (fy(e.y1) + fy(e.y2)) / 2 + 4, e.label)
        # Port labels near each endpoint
        c.setFillColor(MAC_COLOR)
        c.setFont("Helvetica", 6)
        for port, t in ((e.a_port, 0.25), (e.b_port, 0.75)):
            if port:
                px, py = point_along(e.x1, fy(e.y1), e.x2, fy(e.y2), t)
                c.drawCentredString(px, py + 3, port)
    c.setDash([])

    # Node cards
    for n in scene.nodes:
        card = n.card
        top = fy(n.y)  # PDF y of the card's top edge

        # Background + border, via a path so it can be reused for clipping
        card_path = c.beginPath()
        card_path.roundRect(n.x, top - card.height, card.width, card.height, 6)
        # Dim devices excluded by an active VLAN filter, mirroring the canvas.
        if card.matches_filter:
            c.setFillColor(HexColor(card.fill))
            c.setStrokeColor(HexColor(card.stroke))
        else:
            c.setFillColor(HexColor(card.fill), alpha=0.25)
            c.setStrokeColor(HexColor(MUTED_COLOR))
        c.drawPath(card_path, fill=1, stroke=1)

        if card.striped:
            _draw_status_stripes(c, card_path, n.x, top, card.width, card.height, card.stripe_colors)

        # Header: glyph is skipped in PDF (Helvetica lacks many glyphs); bold name
        c.setFillColor(TEXT_COLOR)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(n.x + 8, top - HEADER_H / 2 - 4, card.name)

        y = top - HEADER_H

        # Device model: small italic line under the name, only when set
        if card.device_model:
            c.setFillColor(HexColor("#555555"))
            c.setFont("Helvetica-Oblique", 7)
            c.drawString(n.x + 8, y - MODEL_H / 2 - 3, card.device_model)
            y -= MODEL_H

        # Type band
        c.setFillColor(HexColor(card.stroke))
        c.rect(n.x, y - TYPE_BAND_H, card.width, TYPE_BAND_H, stroke=0, fill=1)
        c.setFillColor(HexColor("#ffffff"))
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(n.x + card.width / 2, y - TYPE_BAND_H / 2 - 2.5, card.type_label.upper())
        y -= TYPE_BAND_H

        # Native VLAN: always shown (device-wide default is VLAN 1)
        c.setFillColor(HexColor("#333333"))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(n.x + 8, y - NATIVE_VLAN_H / 2 - 3, card.native_vlan_line)
        y -= NATIVE_VLAN_H

        # Loopback IP: single bold line, only when set
        if card.loopback_line:
            c.setFillColor(HexColor("#333333"))
            c.setFont("Helvetica-Bold", 7)
            c.drawString(n.x + 8, y - LOOPBACK_H / 2 - 3, card.loopback_line)
            y -= LOOPBACK_H

        # Config attachment indicator
        if card.config_line:
            c.setFillColor(HexColor("#7627bb"))
            c.setFont("Helvetica-Oblique", 7)
            c.drawString(n.x + 8, y - CONFIG_H / 2 - 2.5, card.config_line)
            y -= CONFIG_H

        # Interface blocks: name+IP, MAC beneath in gray, VLAN beneath that in blue
        third = IFACE_BLOCK_H / 3
        for block in card.iface_blocks:
            c.setFillColor(TEXT_COLOR)
            c.setFont("Helvetica", 8)
            c.drawString(n.x + 8, y - third * 1 + 2, block.top)
            c.setFillColor(MAC_COLOR)
            c.setFont("Helvetica", 6.5)
            c.drawString(n.x + 16, y - third * 2 + 2, block.mac)
            chip_x = n.x + 16
            chip_y = y - third * 3 + 1.5
            for chip_color in block.vlan_colors:
                c.setFillColor(HexColor(MUTED_COLOR if not block.matches_filter else chip_color))
                c.rect(chip_x, chip_y, VLAN_CHIP_W, VLAN_CHIP_H, stroke=0, fill=1)
                chip_x += VLAN_CHIP_W + VLAN_CHIP_GAP
            text_x = chip_x + 3 if block.vlan_colors else n.x + 16
            c.setFillColor(HexColor(MUTED_TEXT if not block.matches_filter else "#1a56db"))
            c.setFont("Helvetica", 6.5)
            c.drawString(text_x, y - third * 3 + 2, block.vlan)
            y -= IFACE_BLOCK_H

        if card.more_count:
            c.setFillColor(MAC_COLOR)
            c.setFont("Helvetica-Oblique", 7)
            c.drawString(n.x + 8, y - FOOTER_H / 2 - 3, f"+{card.more_count} more…")
            y -= FOOTER_H

        # Notes: wrapped lines below a separator, only when set
        if card.notes_lines:
            y -= PAD / 2
            c.setStrokeColor(HexColor(card.stroke))
            c.line(n.x + 4, y, n.x + card.width - 4, y)
            y -= 2
            c.setFillColor(HexColor("#444444"))
            c.setFont("Helvetica-Oblique", 7)
            for line in card.notes_lines:
                c.drawString(n.x + 8, y - NOTES_LINE_H / 2 - 2, line)
                y -= NOTES_LINE_H

    # Text annotations last so they sit above cards if they overlap.
    for text_shape in scene.texts:
        # Light panel behind the text, matching the canvas: keeps an
        # annotation readable where it overlaps a device card.
        line_h = text_shape.font_size * 1.35
        panel_h = len(text_shape.lines) * line_h + 8
        c.setFillColor(HexColor(DIAGRAM_BG))
        c.rect(
            text_shape.x - 4, fy(text_shape.y) - panel_h + 4,
            text_shape.width + 8, panel_h,
            stroke=0, fill=1,
        )
        c.setFillColor(HexColor(text_shape.color))
        font = "Helvetica-Bold" if text_shape.bold else "Helvetica"
        c.setFont(font, text_shape.font_size)
        line_height = text_shape.font_size * 1.35
        ty = fy(text_shape.y) - text_shape.font_size
        for line in text_shape.lines:
            c.drawString(text_shape.x, ty, line)
            ty -= line_height


def _draw_status_stripes(
    c: pdf_canvas.Canvas,
    card_path,
    x: float,
    top: float,
    w: float,
    h: float,
    colors: list[str],
) -> None:
    """Overlay diagonal stripes across a card for its status tag.

    Clips to the card's own rounded-rect path so stripes never spill
    past the border, then draws parallel diagonal lines at a fixed
    spacing, cycling through `colors` per line: PLANNED passes a single
    gray so every stripe matches, BROKEN passes [red, black] so the
    stripes alternate hazard-tape style. The clip is undone via
    saveState/restoreState so it doesn't leak onto other cards or the
    plan title.
    """
    c.saveState()
    c.clipPath(card_path, stroke=0, fill=0)
    c.setLineWidth(STRIPE_WIDTH)

    span = w + h
    offset = -span
    line_index = 0
    while offset < span:
        # NB: alpha must be passed to setStrokeColor directly; a bare
        # setStrokeAlpha() call is silently overridden by the next
        # setStrokeColor in this reportlab version.
        c.setStrokeColor(HexColor(colors[line_index % len(colors)]), alpha=STRIPE_ALPHA)
        c.line(x + offset, top, x + offset + h, top - h)
        offset += STRIPE_SPACING
        line_index += 1

    c.restoreState()
