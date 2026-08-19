"""Auto-layout for network diagrams.

Uses networkx's built-in layouts by default. If pygraphviz is installed
(`pip install netplanner[layout]`), graphviz "dot"/"neato" layouts can be
used for cleaner hierarchical results.
"""

from __future__ import annotations

import networkx as nx

from .model import NetworkPlan

CANVAS_SCALE = 400.0  # spread normalized coords across the canvas


def auto_layout(plan: NetworkPlan, algorithm: str = "spring") -> None:
    """Assign x/y positions to every device in-place."""
    if plan.graph.number_of_nodes() == 0:
        return

    if algorithm == "spring":
        pos = nx.spring_layout(plan.graph, seed=42)
    elif algorithm == "circular":
        pos = nx.circular_layout(plan.graph)
    elif algorithm == "kamada_kawai":
        pos = nx.kamada_kawai_layout(plan.graph)
    else:
        raise ValueError(f"Unknown layout algorithm: {algorithm}")

    for node_id, (x, y) in pos.items():
        device = plan.graph.nodes[node_id]["device"]
        device.x = float(x) * CANVAS_SCALE
        device.y = float(y) * CANVAS_SCALE
