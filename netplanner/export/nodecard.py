"""Shared node "card" layout for device rendering.

Devices are drawn as three-section cards, identically in the GUI and
in PDF/PNG exports:

    +---------------------+
    | name        (glyph) |   header
    |=====================|
    |     device type     |   type band (filled with the type color)
    |=====================|
    | eth0  10.0.1.1/24   |   one block per interface:
    |   02:AB:CD:12:34:56 |   name + IP line, MAC line beneath
    | ...                 |
    | +N more...          |   overflow indicator past MAX_IFACE_BLOCKS
    +---------------------+

Card sizing lives here so the canvas and the renderer always agree on
node geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netplanner.domain.entities import Device

# Geometry constants (canvas pixels == export points).
NODE_W = 180.0
HEADER_H = 24.0
TYPE_BAND_H = 16.0
IFACE_BLOCK_H = 24.0  # IP line + MAC line
FOOTER_H = 14.0       # "+N more..." row
PAD = 6.0
MAX_IFACE_BLOCKS = 6  # cap so many-port switches stay readable


@dataclass
class IfaceBlock:
    """One interface's two display lines."""

    top: str   # "eth0  10.0.1.1/24" (IP omitted when unset)
    mac: str   # "02:AB:CD:12:34:56"


@dataclass
class NodeCard:
    """Everything needed to draw one device node."""

    width: float
    height: float
    name: str
    type_label: str
    glyph: str
    fill: str
    stroke: str
    iface_blocks: list[IfaceBlock] = field(default_factory=list)
    more_count: int = 0  # interfaces hidden past the cap


def build_card(device: Device) -> NodeCard:
    """Compute the card contents and size for a device."""
    from .styles import style_for  # local import avoids a cycle at module load

    style = style_for(device.device_type)

    blocks = []
    for iface in device.interfaces[:MAX_IFACE_BLOCKS]:
        ip = f"  {iface.ip_address}" if iface.ip_address else ""
        blocks.append(IfaceBlock(top=f"{iface.name}{ip}", mac=iface.mac_address))
    more = max(0, len(device.interfaces) - MAX_IFACE_BLOCKS)

    height = HEADER_H + TYPE_BAND_H + len(blocks) * IFACE_BLOCK_H + PAD
    if more:
        height += FOOTER_H

    return NodeCard(
        width=NODE_W,
        height=height,
        name=device.name,
        type_label=device.device_type.value.replace("_", " "),
        glyph=style.glyph,
        fill=style.fill,
        stroke=style.stroke,
        iface_blocks=blocks,
        more_count=more,
    )
