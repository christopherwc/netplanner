"""Shared diagram renderer.

Converts a NetworkPlan into backend-agnostic drawing primitives so the
PDF and PNG exporters (and potentially the GUI canvas) produce visually
identical output.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class Scene:
    width: float
    height: float
    nodes: list[NodeShape]
    edges: list[EdgeShape]
    title: str


def build_scene(plan: NetworkPlan) -> Scene:
    """Convert a plan into positioned, styled drawing primitives.

    Coordinates are normalized so the whole diagram fits in the output
    page with a margin, regardless of where devices sit on the canvas.
    Parallel links receive the same fan-out offsets the GUI uses.
    """
    devices = plan.devices
    if not devices:
        return Scene(width=400, height=300, nodes=[], edges=[], title=plan.name)

    # Build each device's card first so we know its actual footprint —
    # cards vary in height (interfaces, notes, etc.) and can be wider
    # than the margin, so normalizing by device *center* alone would
    # clip whichever card sticks out furthest past its center.
    cards = {d.id: build_card(d) for d in devices}

    # Find how far each card's top-left corner sits from its device's
    # raw (x, y) center, then shift everything so the single
    # leftmost/topmost card edge — not just the leftmost/topmost
    # center — lands exactly at MARGIN.
    min_left = min(d.x - cards[d.id].width / 2 for d in devices)
    min_top = min(d.y - cards[d.id].height / 2 for d in devices)
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
            )
        )

    width = max(n.x + n.card.width for n in nodes) + MARGIN
    height = max(n.y + n.card.height for n in nodes) + MARGIN
    return Scene(width=width, height=height, nodes=nodes, edges=edges, title=plan.name)


def _port_name(plan: NetworkPlan, device_id: str, interface_id: str | None) -> str:
    if not interface_id:
        return ""
    device = plan.get_device(device_id)
    if device is None:
        return ""
    iface = next((i for i in device.interfaces if i.id == interface_id), None)
    return iface.name if iface else ""
