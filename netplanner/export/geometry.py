"""Shared link geometry used by the GUI canvas and the exporters.

When several links connect the same pair of devices, drawing them all
center-to-center would stack them into one line. parallel_link_offsets
assigns each link a signed perpendicular offset so parallel links fan
out and never overlap; offset_endpoints applies it to a segment.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from netplanner.domain.entities import Link

SPACING = 18.0  # px between parallel links


def parallel_link_offsets(links: Iterable[Link]) -> dict[str, float]:
    """Return {link.id: signed perpendicular offset}.

    Offsets are centered around zero: 1 link -> [0], 2 -> [-9, +9],
    3 -> [-18, 0, +18], etc. The sign is normalized to the canonical
    device order so links stored as (b, a) still fan out consistently
    with their (a, b) siblings.
    """
    groups: dict[frozenset, list[Link]] = {}
    for link in links:
        if link.a_device_id == link.b_device_id:
            continue  # ignore self-loops
        groups.setdefault(frozenset((link.a_device_id, link.b_device_id)), []).append(link)

    offsets: dict[str, float] = {}
    for group in groups.values():
        n = len(group)
        for i, link in enumerate(group):
            offset = (i - (n - 1) / 2) * SPACING
            if link.a_device_id > link.b_device_id:
                offset = -offset  # normalize direction
            offsets[link.id] = offset
    return offsets



def offset_endpoints(
    x1: float, y1: float, x2: float, y2: float, offset: float
) -> tuple[float, float, float, float]:
    """Shift both endpoints perpendicular to the segment by `offset`."""
    if offset == 0:
        return x1, y1, x2, y2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return x1, y1, x2, y2
    px, py = -dy / length, dx / length
    return x1 + px * offset, y1 + py * offset, x2 + px * offset, y2 + py * offset


def point_along(
    x1: float, y1: float, x2: float, y2: float, t: float
) -> tuple[float, float]:
    """Point at fraction t (0..1) along the segment; used for port labels."""
    return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t


def card_exit_point(
    cx: float,
    cy: float,
    tx: float,
    ty: float,
    half_w: float,
    half_h: float,
    gap: float = 6.0,
) -> tuple[float, float]:
    """Point just outside a card's edge, along the line toward (tx, ty).

    Link lines run center-to-center and are drawn beneath cards, so a
    port label placed at a fixed fraction along the line (25% / 75%)
    disappears under the card whenever the two devices are close
    together or the cards are tall. Anchoring to the card's boundary
    instead means the label always sits in open space next to the
    device it belongs to, regardless of link length.

    Solves for where the ray from the card center crosses the
    axis-aligned card rectangle, then steps `gap` further along it.
    """
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy

    # Scale needed to reach each edge; the nearer one is the real exit.
    scale_x = half_w / abs(dx) if dx else float("inf")
    scale_y = half_h / abs(dy) if dy else float("inf")
    scale = min(scale_x, scale_y)

    ex, ey = cx + dx * scale, cy + dy * scale
    length = (dx * dx + dy * dy) ** 0.5
    return ex + dx / length * gap, ey + dy / length * gap


def lift_above_line(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    amount: float,
) -> tuple[float, float]:
    """Move a point off a line, perpendicular, toward the top of the page.

    Labels centered on a cable sit *on* it, so the line strikes through
    the text. Offsetting perpendicular keeps the label associated with
    its cable while leaving the cable unbroken. Of the two perpendicular
    directions the upward one is chosen so labels read consistently
    rather than flipping side with the link's direction.
    """
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return x, y - amount
    # Perpendicular unit vector, normalized to point "up" the page.
    px, py = -dy / length, dx / length
    if py > 0:
        px, py = -px, -py
    return x + px * amount, y + py * amount


def label_anchor(
    cx: float,
    cy: float,
    tx: float,
    ty: float,
    half_w: float,
    half_h: float,
    text_w: float,
    text_h: float,
    gap: float = 6.0,
    lift: float = 0.0,
) -> tuple[float, float]:
    """Center point for a port label that clears the card entirely.

    card_exit_point() lands on the card's boundary, so a label centered
    there still has half its width inside the card. This pushes the
    center further along the link direction by the label's own
    half-extent, so the whole label sits in open space. `lift` then
    raises it clear of the cable itself.
    """
    ex, ey = card_exit_point(cx, cy, tx, ty, half_w, half_h, gap)
    dx, dy = tx - cx, ty - cy
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return ex, ey
    ux, uy = dx / length, dy / length
    ax, ay = ex + ux * text_w / 2, ey + uy * text_h / 2
    if lift:
        ax, ay = lift_above_line(ax, ay, cx, cy, tx, ty, lift)
    return ax, ay
