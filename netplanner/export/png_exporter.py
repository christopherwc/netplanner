"""Export a NetworkPlan to PNG using Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from netplanner.domain.model import NetworkPlan

from .renderer import build_scene
from .styles import style_for_value

SCALE = 2  # supersample for crisper output
NODE_FILL = "#e8f0fe"
NODE_STROKE = "#1a56db"
EDGE_COLOR = "#555555"
TEXT_COLOR = "#111111"
BG_COLOR = "#ffffff"


def export_png(plan: NetworkPlan, path: Path) -> None:
    scene = build_scene(plan)
    w, h = int(scene.width) * SCALE, (int(scene.height) + 40) * SCALE
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Title
    draw.text((20 * SCALE, 12 * SCALE), scene.title, fill=TEXT_COLOR)

    off = 40 * SCALE  # room for the title bar

    # Edges under nodes
    for e in scene.edges:
        draw.line(
            (e.x1 * SCALE, e.y1 * SCALE + off, e.x2 * SCALE, e.y2 * SCALE + off),
            fill=EDGE_COLOR,
            width=2 * SCALE,
        )

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
