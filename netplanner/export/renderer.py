"""Shared diagram renderer.

Converts a NetworkPlan into backend-agnostic drawing primitives so the
PDF and PNG exporters (and potentially the GUI canvas) produce visually
identical output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netplanner.domain.model import NetworkPlan

from .geometry import offset_endpoints, parallel_link_offsets
from .nodecard import NodeCard, build_card

MARGIN = 60.0


@dataclass
class NodeShape:
    """A positioned device card ready for drawing."""

    x: float  # top-left
    y: float
    card: NodeCard


@dataclass
class EdgeShape:
    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    link_type: str = "ethernet"
    a_port: str = ""
    b_port: str = ""
    # Half-extents of the card at each end, so renderers can anchor port
    # labels just outside the card instead of at a fixed fraction along
    # the line (which lands under the card on short links).
    a_half: tuple[float, float] = (0.0, 0.0)
    b_half: tuple[float, float] = (0.0, 0.0)


@dataclass
class SiteShape:
    """A positioned site box ready for drawing."""

    x: float
    y: float
    width: float
    height: float
    name: str
    notes_lines: list[str]
    color: str


@dataclass
class UnderlayShape:
    """A positioned background drawing ready for drawing.

    Carries the encoded PNG and the opacity rather than a faded copy:
    decoding and fading need Pillow, and belong inside the exporters'
    error handling, where a damaged picture becomes an ExportError
    naming the plan. See export/underlays.py for the shared fade.
    """

    x: float
    y: float
    width: float
    height: float
    image_png_b64: str
    opacity: float


@dataclass
class TextShape:
    """A positioned text annotation ready for drawing."""

    x: float  # top-left
    y: float
    lines: list[str]  # already wrapped by TextBox.display_lines
    font_size: float
    bold: bool
    color: str
    width: float = 200.0  # wrap width, also the background panel width


@dataclass
class Scene:
    width: float
    height: float
    nodes: list[NodeShape]
    edges: list[EdgeShape]
    title: str
    texts: list[TextShape] = field(default_factory=list)
    sites: list[SiteShape] = field(default_factory=list)
    underlays: list[UnderlayShape] = field(default_factory=list)


def build_scene(plan: NetworkPlan, vlan_filter: set[int] | None = None) -> Scene:
    """Convert a plan into positioned, styled drawing primitives.

    Coordinates are normalized so the whole diagram fits in the output
    page with a margin, regardless of where devices sit on the canvas.
    Parallel links receive the same fan-out offsets the GUI uses.
    """
    devices = plan.devices
    boxes = list(plan.textboxes.values())
    site_list = list(plan.sites.values())
    underlay_list = list(plan.underlays.values())

    # Build each device's card first so we know its actual footprint —
    # cards vary in height (interfaces, notes, etc.) and can be wider
    # than the margin, so normalizing by device *center* alone would
    # clip whichever card sticks out furthest past its center.
    cards = {d.id: build_card(d, vlan_filter) for d in devices}

    # Every element's box as (left, top, right, bottom) in canvas
    # coordinates. Annotations, sites and underlays use top-left
    # coordinates and routinely sit outside the device cluster — an
    # underlay usually *is* the outer edge of the drawing — so all of
    # them take part in the bounds, or they would be cropped. A plan can
    # legitimately hold no devices at all (a floor plan with only its
    # labels so far), and the same bounds cover that case.
    extents = [
        (
            d.x - cards[d.id].width / 2, d.y - cards[d.id].height / 2,
            d.x + cards[d.id].width / 2, d.y + cards[d.id].height / 2,
        )
        for d in devices
    ]
    extents += [(b.x, b.y, b.x + b.width, b.y + b.height) for b in boxes]
    extents += [(s.x, s.y, s.x + s.width, s.y + s.height) for s in site_list]
    extents += [(u.x, u.y, u.x + u.width, u.y + u.height) for u in underlay_list]
    if not extents:
        return Scene(width=400, height=300, nodes=[], edges=[], title=plan.name)

    # Shift everything so the single leftmost/topmost edge lands exactly
    # at MARGIN.
    shift_x = MARGIN - min(e[0] for e in extents)
    shift_y = MARGIN - min(e[1] for e in extents)

    centers = {d.id: (d.x + shift_x, d.y + shift_y) for d in devices}

    nodes = []
    for d in devices:
        cx, cy = centers[d.id]
        card = cards[d.id]
        nodes.append(NodeShape(x=cx - card.width / 2, y=cy - card.height / 2, card=card))

    edges = []
    offsets = parallel_link_offsets(plan.links)
    for link in plan.links:
        (x1, y1) = centers[link.a_device_id]
        (x2, y2) = centers[link.b_device_id]
        x1, y1, x2, y2 = offset_endpoints(x1, y1, x2, y2, offsets.get(link.id, 0.0))
        edges.append(
            EdgeShape(
                x1, y1, x2, y2,
                label=link.label,
                link_type=link.link_type.value,
                a_port=_port_name(plan, link.a_device_id, link.a_interface_id),
                b_port=_port_name(plan, link.b_device_id, link.b_interface_id),
                a_half=(cards[link.a_device_id].width / 2, cards[link.a_device_id].height / 2),
                b_half=(cards[link.b_device_id].width / 2, cards[link.b_device_id].height / 2),
            )
        )

    texts = [
        TextShape(
            x=b.x + shift_x,
            y=b.y + shift_y,
            lines=b.display_lines,
            font_size=b.font_size,
            bold=b.bold,
            color=b.color,
            width=b.width,
        )
        for b in boxes
    ]

    sites = [
        SiteShape(
            x=s.x + shift_x, y=s.y + shift_y, width=s.width, height=s.height,
            name=s.name, notes_lines=_wrap_site_notes(s), color=s.color,
        )
        for s in site_list
    ]

    underlays = [
        UnderlayShape(
            x=u.x + shift_x, y=u.y + shift_y, width=u.width, height=u.height,
            image_png_b64=u.image_png_b64, opacity=u.opacity,
        )
        for u in underlay_list
    ]

    return Scene(
        width=max(e[2] for e in extents) + shift_x + MARGIN,
        height=max(e[3] for e in extents) + shift_y + MARGIN,
        nodes=nodes, edges=edges,
        title=plan.name, texts=texts, sites=sites, underlays=underlays,
    )


def _wrap_site_notes(site, max_lines: int = 4) -> list[str]:
    """Wrap a site's notes to its box width, capped like the canvas does."""
    if not site.notes:
        return []
    chars = max(12, int((site.width - 16) / 6))
    lines: list[str] = []
    for paragraph in site.notes.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) > chars and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "\u2026"
    return lines


def _port_name(plan: NetworkPlan, device_id: str, interface_id: str | None) -> str:
    if not interface_id:
        return ""
    device = plan.get_device(device_id)
    if device is None:
        return ""
    iface = next((i for i in device.interfaces if i.id == interface_id), None)
    return iface.name if iface else ""
