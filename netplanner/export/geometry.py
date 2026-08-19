"""Shared link geometry used by the GUI canvas and the exporters.

When several links connect the same pair of devices, drawing them all
center-to-center would stack them into one line. parallel_link_offsets
assigns each link a signed perpendicular offset so parallel links fan
out and never overlap; offset_endpoints applies it to a segment.
"""

from __future__ import annotations

import math
from typing import Iterable

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
