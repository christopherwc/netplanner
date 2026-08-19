"""Export a NetworkPlan to PDF using reportlab.

reportlab's coordinate origin is bottom-left, while the scene uses
top-left; fy() flips the axis. Nodes are drawn as the same three-section
cards the GUI shows (header / type band / interface IP+MAC blocks).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas as pdf_canvas

from netplanner.domain.model import NetworkPlan

from .geometry import point_along
from .nodecard import (
    FOOTER_H,
    HEADER_H,
    IFACE_BLOCK_H,
    LOOPBACK_H,
    MODEL_H,
    NATIVE_VLAN_H,
    NOTES_LINE_H,
    PAD,
    TYPE_BAND_H,
)
from .renderer import Scene, build_scene
from .styles import link_style_for_value

TEXT_COLOR = HexColor("#111111")
MAC_COLOR = HexColor("#777777")
TITLE_OFFSET = 40  # room reserved above the diagram for the plan title


def export_pdf(plan: NetworkPlan, path: Path) -> None:
    """Render the plan to a single-page PDF sized to fit the diagram."""
    scene = build_scene(plan)
    c = pdf_canvas.Canvas(str(path), pagesize=(scene.width, scene.height + TITLE_OFFSET))
    _draw(c, scene)
    c.showPage()
    c.save()


def _draw(c: pdf_canvas.Canvas, scene: Scene) -> None:
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

        # Background + border
        c.setFillColor(HexColor(card.fill))
        c.setStrokeColor(HexColor(card.stroke))
        c.roundRect(n.x, top - card.height, card.width, card.height, 6, stroke=1, fill=1)

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

        # Interface blocks: name+IP, MAC beneath in gray, VLAN beneath that in blue
        third = IFACE_BLOCK_H / 3
        for block in card.iface_blocks:
            c.setFillColor(TEXT_COLOR)
            c.setFont("Helvetica", 8)
            c.drawString(n.x + 8, y - third * 1 + 2, block.top)
            c.setFillColor(MAC_COLOR)
            c.setFont("Helvetica", 6.5)
            c.drawString(n.x + 16, y - third * 2 + 2, block.mac)
            c.setFillColor(HexColor("#1a56db"))
            c.setFont("Helvetica", 6.5)
            c.drawString(n.x + 16, y - third * 3 + 2, block.vlan)
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
