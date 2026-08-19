"""Export a NetworkPlan to PDF using reportlab."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas as pdf_canvas

from netplanner.domain.model import NetworkPlan

from .geometry import point_along
from .renderer import Scene, build_scene
from .styles import link_style_for_value, style_for_value

NODE_FILL = HexColor("#e8f0fe")
NODE_STROKE = HexColor("#1a56db")
EDGE_COLOR = HexColor("#555555")
TEXT_COLOR = HexColor("#111111")


def export_pdf(plan: NetworkPlan, path: Path) -> None:
    """Render the plan to a single-page PDF sized to fit the diagram."""
    scene = build_scene(plan)
    c = pdf_canvas.Canvas(str(path), pagesize=(scene.width, scene.height + 40))
    _draw(c, scene)
    c.showPage()
    c.save()


def _draw(c: pdf_canvas.Canvas, scene: Scene) -> None:
    page_h = scene.height + 40

    def fy(y: float) -> float:
        """Flip y: scene uses top-left origin, PDF uses bottom-left."""
        return page_h - y

    # Title
    c.setFillColor(TEXT_COLOR)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20, page_h - 25, scene.title)

    # Edges first, under the nodes
    for e in scene.edges:
        lstyle = link_style_for_value(e.link_type)
        c.setStrokeColor(HexColor(lstyle.color))
        c.setLineWidth(lstyle.width)
        c.setDash(list(lstyle.dash) if lstyle.dash else [])
        c.line(e.x1, fy(e.y1) - 40, e.x2, fy(e.y2) - 40)
        if e.label:
            c.setFillColor(HexColor(lstyle.color))
            c.setFont("Helvetica", 7)
            c.drawCentredString((e.x1 + e.x2) / 2, (fy(e.y1) + fy(e.y2)) / 2 - 40 + 4, e.label)
        c.setFillColor(HexColor("#666666"))
        c.setFont("Helvetica", 6)
        for port, t in ((e.a_port, 0.25), (e.b_port, 0.75)):
            if port:
                px, py = point_along(e.x1, fy(e.y1) - 40, e.x2, fy(e.y2) - 40, t)
                c.drawCentredString(px, py + 3, port)
    c.setDash([])

    # Nodes
    for n in scene.nodes:
        style = style_for_value(n.sublabel)
        top = fy(n.y) - 40
        c.setFillColor(HexColor(style.fill))
        c.setStrokeColor(HexColor(style.stroke))
        c.roundRect(n.x, top - n.h, n.w, n.h, 6, stroke=1, fill=1)
        c.setFillColor(TEXT_COLOR)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(n.x + n.w / 2, top - n.h / 2 + 4, n.label)
        c.setFont("Helvetica", 8)
        c.drawCentredString(n.x + n.w / 2, top - n.h / 2 - 8, n.sublabel)
