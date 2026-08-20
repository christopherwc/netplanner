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


def build_scene(plan: NetworkPlan, vlan_filter: set[int] | None = None) -> Scene:
    """Convert a plan into positioned, styled drawing primitives.

    Coordinates are normalized so the whole diagram fits in the output
    page with a margin, regardless of where devices sit on the canvas.
    Parallel links receive the same fan-out offsets the GUI uses.
    """
    devices = plan.devices
    boxes = list(plan.textboxes.values())
    if not devices:
        # A plan can legitimately hold only annotations; normalize those
        # on their own rather than returning a fixed empty canvas.
        if not boxes:
            return Scene(width=400, height=300, nodes=[], edges=[], title=plan.name)
        min_left = min(b.x for b in boxes)
        min_top = min(b.y for b in boxes)
        texts = [
            TextShape(
                x=b.x - min_left + MARGIN,
                y=b.y - min_top + MARGIN,
                lines=b.display_lines,
                font_size=b.font_size,
                bold=b.bold,
                color=b.color,
                width=b.width,
            )
            for b in boxes
        ]
        return Scene(
            width=max(t.x + max(b.width for b in boxes) for t in texts) + MARGIN,
            height=max(t.y + b.height for t, b in zip(texts, boxes)) + MARGIN,
            nodes=[], edges=[], title=plan.name, texts=texts,
        )

    # Build each device's card first so we know its actual footprint —
    # cards vary in height (interfaces, notes, etc.) and can be wider
    # than the margin, so normalizing by device *center* alone would
    # clip whichever card sticks out furthest past its center.
    cards = {d.id: build_card(d, vlan_filter) for d in devices}

    # Find how far each card's top-left corner sits from its device's
    # raw (x, y) center, then shift everything so the single
    # leftmost/topmost card edge — not just the leftmost/topmost
    # center — lands exactly at MARGIN.
    min_left = min(d.x - cards[d.id].width / 2 for d in devices)
    min_top = min(d.y - cards[d.id].height / 2 for d in devices)
    # Annotations use top-left coordinates and can sit outside the device
    # cluster, so they take part in the bounds or they would be clipped.
    if boxes:
        min_left = min(min_left, min(b.x for b in boxes))
        min_top = min(min_top, min(b.y for b in boxes))
    shift_x = MARGIN - min_left
    shift_y = MARGIN - min_top

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

    width = max(n.x + n.card.width for n in nodes) + MARGIN
    height = max(n.y + n.card.height for n in nodes) + MARGIN
    # Grow the page if an annotation extends past the device cluster.
    for text_shape, box in zip(texts, boxes):
        width = max(width, text_shape.x + box.width + MARGIN)
        height = max(height, text_shape.y + box.height + MARGIN)

    return Scene(
        width=width, height=height, nodes=nodes, edges=edges,
        title=plan.name, texts=texts,
    )


def _port_name(plan: NetworkPlan, device_id: str, interface_id: str | None) -> str:
    if not interface_id:
        return ""
    device = plan.get_device(device_id)
    if device is None:
        return ""
    iface = next((i for i in device.interfaces if i.id == interface_id), None)
    return iface.name if iface else ""
