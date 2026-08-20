"""Auto-layout for network diagrams.

Uses networkx's built-in layouts by default; those require numpy
(spring/circular) and scipy (kamada_kawai), which are declared project
dependencies. If they are somehow unavailable — e.g. an environment
installed from an old requirements file — auto_layout degrades to a
dependency-free circle layout instead of raising, so the GUI feature
keeps working rather than crashing the app.
"""

from __future__ import annotations

import logging

import math

import networkx as nx

from .model import NetworkPlan

logger = logging.getLogger(__name__)

CANVAS_SCALE = 400.0  # spread normalized coords across the canvas

#: Algorithms accepted by auto_layout, for callers building menus.
ALGORITHMS = ("spring", "circular", "kamada_kawai")


def auto_layout(plan: NetworkPlan, algorithm: str = "spring") -> None:
    """Assign x/y positions to every device in-place.

    Falls back to a pure-Python circle layout if the chosen networkx
    algorithm's numeric dependencies (numpy/scipy) are missing, so this
    function only raises for a programming error (unknown algorithm
    name), never for an environment problem.

    Raises:
        ValueError: if `algorithm` is not one of ALGORITHMS.
    """
    if plan.graph.number_of_nodes() == 0:
        return

    if algorithm not in ALGORITHMS:
        logger.error("Unknown layout algorithm requested: %r", algorithm)
        raise ValueError(f"Unknown layout algorithm: {algorithm}")

    try:
        if algorithm == "spring":
            pos = nx.spring_layout(plan.graph, seed=42)
        elif algorithm == "circular":
            pos = nx.circular_layout(plan.graph)
        else:  # kamada_kawai
            pos = nx.kamada_kawai_layout(plan.graph)
    except ImportError as exc:
        # numpy/scipy are declared dependencies, so reaching this means a
        # broken environment; degrade visibly rather than crash silently.
        logger.warning(
            "Layout algorithm '%s' unavailable (%s); using circle fallback",
            algorithm, exc,
        )
        pos = _fallback_circle_layout(plan)

    for node_id, (x, y) in pos.items():
        device = plan.graph.nodes[node_id]["device"]
        device.x = float(x) * CANVAS_SCALE
        device.y = float(y) * CANVAS_SCALE


def _fallback_circle_layout(plan: NetworkPlan) -> dict[str, tuple[float, float]]:
    """Evenly space all devices on a unit circle, no numpy required.

    Matches networkx's convention of normalized coordinates in roughly
    [-1, 1] so the CANVAS_SCALE multiplication behaves the same as for
    the real algorithms. A single device sits at the origin.
    """
    node_ids = list(plan.graph.nodes)
    count = len(node_ids)
    if count == 1:
        return {node_ids[0]: (0.0, 0.0)}
    return {
        node_id: (
            math.cos(2 * math.pi * index / count),
            math.sin(2 * math.pi * index / count),
        )
        for index, node_id in enumerate(node_ids)
    }
