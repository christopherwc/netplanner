"""Shared diagram renderer.

Converts a NetworkPlan into backend-agnostic drawing primitives so the
PDF and PNG exporters (and potentially the GUI canvas) produce visually
identical output.
"""

from __future__ import annotations

from dataclasses import dataclass

from netplanner.domain.model import NetworkPlan

NODE_W = 120.0
NODE_H = 60.0
MARGIN = 60.0


@dataclass
class NodeShape:
    x: float  # top-left
    y: float
    w: float
    h: float
    label: str
    sublabel: str  # device type


@dataclass
class EdgeShape:
    x1: float
    y1: float
    x2: float
    y2: float
    label: str


@dataclass
class Scene:
    width: float
    height: float
    nodes: list[NodeShape]
    edges: list[EdgeShape]
    title: str


def build_scene(plan: NetworkPlan) -> Scene:
    devices = plan.devices
    if not devices:
        return Scene(width=400, height=300, nodes=[], edges=[], title=plan.name)

    # Normalize coordinates so everything fits with a margin
    min_x = min(d.x for d in devices)
    min_y = min(d.y for d in devices)

    def tx(x: float) -> float:
        return x - min_x + MARGIN

    def ty(y: float) -> float:
        return y - min_y + MARGIN

    centers = {d.id: (tx(d.x), ty(d.y)) for d in devices}

    nodes = [
        NodeShape(
            x=cx - NODE_W / 2,
            y=cy - NODE_H / 2,
            w=NODE_W,
            h=NODE_H,
            label=d.name,
            sublabel=d.device_type.value,
        )
        for d, (cx, cy) in ((d, centers[d.id]) for d in devices)
    ]

    edges = []
    for link in plan.links:
        (x1, y1) = centers[link.a_device_id]
        (x2, y2) = centers[link.b_device_id]
        edges.append(EdgeShape(x1, y1, x2, y2, label=link.label))

    width = max(n.x + n.w for n in nodes) + MARGIN
    height = max(n.y + n.h for n in nodes) + MARGIN
    return Scene(width=width, height=height, nodes=nodes, edges=edges, title=plan.name)
