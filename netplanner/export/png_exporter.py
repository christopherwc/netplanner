"""Export a NetworkPlan to PNG using Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from netplanner.domain.model import NetworkPlan

from .geometry import point_along
from .renderer import build_scene
from .styles import link_style_for_value, style_for_value

SCALE = 2  # supersample for crisper output
NODE_FILL = "#e8f0fe"
NODE_STROKE = "#1a56db"
EDGE_COLOR = "#555555"
TEXT_COLOR = "#111111"
BG_COLOR = "#ffffff"


def export_png(plan: NetworkPlan, path: Path) -> None:
    """Render the plan to PNG; drawn at 2x then downsampled for antialiasing."""
    scene = build_scene(plan)
    w, h = int(scene.width) * SCALE, (int(scene.height) + 40) * SCALE
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Title
    draw.text((20 * SCALE, 12 * SCALE), scene.title, fill=TEXT_COLOR)

    off = 40 * SCALE  # room for the title bar

    # Edges under nodes
    for e in scene.edges:
        lstyle = link_style_for_value(e.link_type)
        p1 = (e.x1 * SCALE, e.y1 * SCALE + off)
        p2 = (e.x2 * SCALE, e.y2 * SCALE + off)
        width = max(1, int(lstyle.width * SCALE))
        if lstyle.dash:
            _dashed_line(draw, p1, p2, lstyle.color, width, [v * SCALE for v in lstyle.dash])
        else:
            draw.line((*p1, *p2), fill=lstyle.color, width=width)
        if e.label:
            mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 - 8 * SCALE)
            draw.text(mid, e.label, fill=lstyle.color, anchor="mm")
        for port, t in ((e.a_port, 0.25), (e.b_port, 0.75)):
            if port:
                pp = point_along(p1[0], p1[1], p2[0], p2[1], t)
                draw.text((pp[0], pp[1] - 6 * SCALE), port, fill="#666666", anchor="mm")

    # Nodes
    for n in scene.nodes:
        box = (
            n.x * SCALE,
            n.y * SCALE + off,
            (n.x + n.w) * SCALE,
            (n.y + n.h) * SCALE + off,
        )
        style = style_for_value(n.sublabel)
        draw.rounded_rectangle(box, radius=6 * SCALE, fill=style.fill, outline=style.stroke, width=SCALE)
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        draw.text((cx, cy - 6 * SCALE), n.label, fill=TEXT_COLOR, anchor="mm")
        draw.text((cx, cy + 8 * SCALE), n.sublabel, fill=TEXT_COLOR, anchor="mm")

    # Downsample for antialiasing
    img = img.resize((w // SCALE, h // SCALE), Image.LANCZOS)
    img.save(path, "PNG")


def _dashed_line(draw, p1, p2, color, width, pattern) -> None:
    """Draw a dashed line segment-by-segment (Pillow has no native dashes)."""
    import math

    x1, y1 = p1
    x2, y2 = p2
    total = math.hypot(x2 - x1, y2 - y1)
    if total == 0:
        return
    ux, uy = (x2 - x1) / total, (y2 - y1) / total
    dist = 0.0
    i = 0
    while dist < total:
        seg = min(pattern[i % len(pattern)], total - dist)
        if i % 2 == 0:  # even segments are drawn, odd are gaps
            sx, sy = x1 + ux * dist, y1 + uy * dist
            ex, ey = x1 + ux * (dist + seg), y1 + uy * (dist + seg)
            draw.line((sx, sy, ex, ey), fill=color, width=width)
        dist += seg
        i += 1
