"""Shared node "card" layout for device rendering.

Devices are drawn as multi-section cards, identically in the GUI and
in PDF/PNG exports:

    +-----------------------------+
    | name                (glyph) |   header
    | Cisco ISR 4331               |   device model (optional, under name)
    |==============================|
    |         device type          |   type band (filled with type color)
    |==============================|
    | Native VLAN: 1                |   device-wide native VLAN (always shown)
    | Loopback: 10.255.0.1/32      |   loopback section (optional)
    |------------------------------|
    | eth0  10.0.1.1/24            |   one block per interface:
    |   00:00:00:00:00:00          |     MAC line
    |   VLAN 10                    |     VLAN line: "VLAN 10" or "Trunk: 10,20,30"
    | ...                          |
    | +N more...                   |   overflow past MAX_IFACE_BLOCKS
    |------------------------------|
    | Notes: uplink to core...     |   notes section (optional, wrapped)
    +-----------------------------+

Card sizing lives here so the canvas and the renderer always agree on
node geometry. All sections beyond the header/type-band/native-VLAN are
optional and only take up space when the underlying field is non-empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netplanner.domain.entities import Device

# Geometry constants (canvas pixels == export points).
NODE_W = 190.0
HEADER_H = 22.0
MODEL_H = 14.0          # device-model line, shown under the name when set
TYPE_BAND_H = 16.0
NATIVE_VLAN_H = 16.0    # single line, always shown (defaults to VLAN 1)
LOOPBACK_H = 16.0       # single line, shown when a loopback IP is set
IFACE_BLOCK_H = 34.0    # name+IP line, MAC line, VLAN line
FOOTER_H = 14.0         # "+N more..." row
NOTES_LINE_H = 12.0
NOTES_MAX_LINES = 3     # notes are capped and truncated with an ellipsis
PAD = 6.0
MAX_IFACE_BLOCKS = 6    # cap so many-port switches stay readable
NOTES_CHARS_PER_LINE = 30  # rough wrap width for the notes section


@dataclass
class IfaceBlock:
    """One interface's three display lines."""

    top: str    # "eth0  10.0.1.1/24" (IP omitted when unset)
    mac: str    # "00:00:00:00:00:00"
    vlan: str   # "VLAN 10" or "Trunk: 10,20,30"


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
    device_model: str = ""
    native_vlan_line: str = ""    # "Native VLAN: 1" — always set (default VLAN 1)
    loopback_line: str = ""       # "Loopback: 10.255.0.1/32", or "" if unset
    iface_blocks: list[IfaceBlock] = field(default_factory=list)
    more_count: int = 0           # interfaces hidden past the cap
    notes_lines: list[str] = field(default_factory=list)  # wrapped, pre-truncated


def _wrap_notes(notes: str) -> list[str]:
    """Word-wrap notes to NOTES_CHARS_PER_LINE, capped at NOTES_MAX_LINES.

    The last visible line gets an ellipsis appended when the text was
    truncated, so it's clear there's more in the full notes field.
    """
    words = notes.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > NOTES_CHARS_PER_LINE and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    if len(lines) > NOTES_MAX_LINES:
        visible = lines[:NOTES_MAX_LINES]
        visible[-1] = visible[-1].rstrip() + "…"
        return visible
    return lines


def build_card(device: Device) -> NodeCard:
    """Compute the card contents and size for a device."""
    from .styles import style_for  # local import avoids a cycle at module load

    style = style_for(device.device_type)

    blocks = []
    for iface in device.interfaces[:MAX_IFACE_BLOCKS]:
        ip = f"  {iface.ip_address}" if iface.ip_address else ""
        blocks.append(
            IfaceBlock(top=f"{iface.name}{ip}", mac=iface.mac_address, vlan=iface.vlan_summary())
        )
    more = max(0, len(device.interfaces) - MAX_IFACE_BLOCKS)

    loopback_line = f"Loopback: {device.loopback_ip}" if device.loopback_ip else ""
    native_vlan_line = f"Native VLAN: {device.native_vlan}"
    notes_lines = _wrap_notes(device.notes)

    height = HEADER_H + TYPE_BAND_H + NATIVE_VLAN_H + len(blocks) * IFACE_BLOCK_H + PAD
    if device.device_model:
        height += MODEL_H
    if more:
        height += FOOTER_H
    if loopback_line:
        height += LOOPBACK_H
    if notes_lines:
        height += len(notes_lines) * NOTES_LINE_H + PAD

    return NodeCard(
        width=NODE_W,
        height=height,
        name=device.name,
        type_label=device.device_type.value.replace("_", " "),
        glyph=style.glyph,
        fill=style.fill,
        stroke=style.stroke,
        device_model=device.device_model,
        native_vlan_line=native_vlan_line,
        loopback_line=loopback_line,
        iface_blocks=blocks,
        more_count=more,
        notes_lines=notes_lines,
    )
