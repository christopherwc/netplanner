"""Export a NetworkPlan to PNG using Pillow.

Drawn at 2x resolution then downsampled for antialiasing. Nodes are the
same three-section cards the GUI shows (header / type band / interface
IP+MAC blocks); Pillow has no native dashed lines, so dashes are drawn
segment-by-segment.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from netplanner.domain.model import NetworkPlan

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
from .renderer import build_scene
from .styles import link_style_for_value

SCALE = 2  # supersample factor for crisper output
TEXT_COLOR = "#111111"
MAC_COLOR = "#777777"
BG_COLOR = "#ffffff"
TITLE_OFFSET = 40  # room reserved above the diagram for the plan title


def export_png(plan: NetworkPlan, path: Path) -> None:
    """Render the plan to PNG; drawn at 2x then downsampled for antialiasing."""
    scene = build_scene(plan)
    w, h = int(scene.width) * SCALE, (int(scene.height) + TITLE_OFFSET) * SCALE
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.text((20 * SCALE, 12 * SCALE), scene.title, fill=TEXT_COLOR)
    off = TITLE_OFFSET * SCALE  # vertical shift below the title area

    # Edges first so they render under the node cards
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
                draw.text((pp[0], pp[1] - 6 * SCALE), port, fill=MAC_COLOR, anchor="mm")

    # Node cards
    for n in scene.nodes:
        card = n.card
        left = n.x * SCALE
        top = n.y * SCALE + off
        right = left + card.width * SCALE
        bottom = top + card.height * SCALE

        # Background + border
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=6 * SCALE,
            fill=card.fill,
            outline=card.stroke,
            width=SCALE,
        )

        if card.striped:
            _draw_status_stripes(img, left, top, card.width * SCALE, card.height * SCALE, card.stripe_colors)

        # Header: bold-ish name (default font; Pillow has no true bold here)
        draw.text(
            (left + 8 * SCALE, top + HEADER_H * SCALE / 2),
            card.name,
            fill=TEXT_COLOR,
            anchor="lm",
        )

        y = top + HEADER_H * SCALE

        # Device model: small italic-ish line under the name, only when set
        if card.device_model:
            draw.text(
                (left + 8 * SCALE, y + MODEL_H * SCALE / 2),
                card.device_model,
                fill="#555555",
                anchor="lm",
            )
            y += MODEL_H * SCALE

        # Type band
        draw.rectangle(
            (left, y, right, y + TYPE_BAND_H * SCALE),
            fill=card.stroke,
        )
        draw.text(
            ((left + right) / 2, y + TYPE_BAND_H * SCALE / 2),
            card.type_label.upper(),
            fill="#ffffff",
            anchor="mm",
        )
        y += TYPE_BAND_H * SCALE

        # Native VLAN: always shown (device-wide default is VLAN 1)
        draw.text(
            (left + 8 * SCALE, y + NATIVE_VLAN_H * SCALE / 2),
            card.native_vlan_line,
            fill="#333333",
            anchor="lm",
        )
        y += NATIVE_VLAN_H * SCALE

        # Loopback IP: single line, only when set
        if card.loopback_line:
            draw.text(
                (left + 8 * SCALE, y + LOOPBACK_H * SCALE / 2),
                card.loopback_line,
                fill="#333333",
                anchor="lm",
            )
            y += LOOPBACK_H * SCALE

        # Config attachment indicator
        if card.config_line:
            draw.text(
                (left + 8 * SCALE, y + CONFIG_H * SCALE / 2),
                card.config_line,
                fill="#7627bb",
                anchor="lm",
            )
            y += CONFIG_H * SCALE

        # Interface blocks: name+IP, MAC beneath in gray, VLAN beneath that in blue
        for block in card.iface_blocks:
            draw.text(
                (left + 8 * SCALE, y + IFACE_BLOCK_H * SCALE * 0.18),
                block.top,
                fill=TEXT_COLOR,
                anchor="lm",
            )
            draw.text(
                (left + 16 * SCALE, y + IFACE_BLOCK_H * SCALE * 0.5),
                block.mac,
                fill=MAC_COLOR,
                anchor="lm",
            )
            draw.text(
                (left + 16 * SCALE, y + IFACE_BLOCK_H * SCALE * 0.82),
                block.vlan,
                fill="#1a56db",
                anchor="lm",
            )
            y += IFACE_BLOCK_H * SCALE

        if card.more_count:
            draw.text(
                (left + 8 * SCALE, y + FOOTER_H * SCALE / 2),
                f"+{card.more_count} more…",
                fill=MAC_COLOR,
                anchor="lm",
            )
            y += FOOTER_H * SCALE

        # Notes: wrapped lines below a separator, only when set
        if card.notes_lines:
            y += PAD * SCALE / 2
            draw.line((left + 4 * SCALE, y, right - 4 * SCALE, y), fill=card.stroke, width=SCALE)
            y += 2 * SCALE
            for line in card.notes_lines:
                draw.text(
                    (left + 8 * SCALE, y + NOTES_LINE_H * SCALE / 2),
                    line,
                    fill="#444444",
                    anchor="lm",
                )
                y += NOTES_LINE_H * SCALE

    # Downsample for antialiasing
    img = img.resize((w // SCALE, h // SCALE), Image.LANCZOS)
    img.save(path, "PNG")


def _draw_status_stripes(
    img: Image.Image, left: float, top: float, w: float, h: float, colors: list[str]
) -> None:
    """Overlay diagonal stripes across a card for its status tag.

    Pillow has no native path clipping, so this composites with masks:
    for each color in `colors`, a mask marks only that color's diagonal
    lines (every len(colors)-th line, offset by the color's position,
    so multiple colors interleave — e.g. red/black alternating for
    BROKEN). Each per-color mask is multiplied by a rounded-rect shape
    mask so stripes stay inside the card, then pasted onto the main
    image — leaving everything else untouched.
    """
    w_i, h_i = int(w), int(h)
    if w_i <= 0 or h_i <= 0 or not colors:
        return

    shape_mask = Image.new("L", (w_i, h_i), 0)
    ImageDraw.Draw(shape_mask).rounded_rectangle((0, 0, w_i, h_i), radius=6 * SCALE, fill=255)

    span = w_i + h_i
    step = STRIPE_SPACING * SCALE
    width = max(1, int(STRIPE_WIDTH * SCALE))

    for color_index, color in enumerate(colors):
        stripe_mask = Image.new("L", (w_i, h_i), 0)
        stripe_draw = ImageDraw.Draw(stripe_mask)
        # Start at this color's slot and jump len(colors) slots per
        # line so the colors interleave rather than overpaint.
        offset = -span + color_index * step
        while offset < span:
            stripe_draw.line((offset, 0, offset + h_i, h_i), fill=255, width=width)
            offset += step * len(colors)

        combined_mask = ImageChops.multiply(stripe_mask, shape_mask)
        # Scale the mask by the stripe alpha so paste() blends the
        # stripes semi-transparently, keeping card text readable.
        combined_mask = combined_mask.point(lambda v: int(v * STRIPE_ALPHA))
        stripe_fill = Image.new("RGB", (w_i, h_i), color)
        img.paste(stripe_fill, (int(left), int(top)), mask=combined_mask)


def _dashed_line(draw, p1, p2, color, width, pattern) -> None:
    """Draw a dashed line segment-by-segment (Pillow has no native dashes).

    Even-indexed pattern entries are drawn, odd-indexed are gaps, matching
    the (on, off, on, off, ...) convention used by Qt and reportlab.
    """
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
        if i % 2 == 0:
            sx, sy = x1 + ux * dist, y1 + uy * dist
            ex, ey = x1 + ux * (dist + seg), y1 + uy * (dist + seg)
            draw.line((sx, sy, ex, ey), fill=color, width=width)
        dist += seg
        i += 1
