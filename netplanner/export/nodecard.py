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
    | 2 config files attached      |   config indicator (optional)
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

Every device also carries a DeviceStatus tag that changes how the whole
card is painted, independent of its layout:
    - ACTIVE:  normal type colors, no overlay (the default).
    - PLANNED: normal type colors, plus a diagonal gray stripe overlay
               drawn across the card by each renderer.
    - BROKEN:  normal type colors, plus a diagonal stripe overlay
               alternating red and black (hazard-tape style) so failed
               equipment stands out at a glance.
This module only computes *what* to draw (fill/stroke/stripe_colors);
the actual stripe painting happens per-renderer since Qt, reportlab,
and Pillow each clip shapes differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netplanner.domain.entities import Device, DeviceStatus

# Geometry constants (canvas pixels == export points).
NODE_W = 190.0
HEADER_H = 22.0
MODEL_H = 14.0          # device-model line, shown under the name when set
TYPE_BAND_H = 16.0
NATIVE_VLAN_H = 16.0    # single line, always shown (defaults to VLAN 1)
LOOPBACK_H = 16.0       # single line, shown when a loopback IP is set
CONFIG_H = 14.0         # single line, shown when configs are attached
IFACE_BLOCK_H = 34.0    # name+IP line, MAC line, VLAN line
FOOTER_H = 14.0         # "+N more..." row
NOTES_LINE_H = 12.0
NOTES_MAX_LINES = 3     # notes are capped and truncated with an ellipsis
PAD = 6.0
MAX_IFACE_BLOCKS = 6    # cap so many-port switches stay readable
NOTES_CHARS_PER_LINE = 30  # rough wrap width for the notes section
MAX_VLAN_CHIPS = 6      # colour chips drawn per interface before truncating
VLAN_CHIP_W = 5.0       # width of one VLAN colour chip
VLAN_CHIP_H = 5.0
VLAN_CHIP_GAP = 1.5

# Status tag styling — shared by canvas + both exporters so a device
# looks identical everywhere regardless of its deployment status.
# Renderers draw diagonal stripes by cycling through a card's
# stripe_colors list, one color per line, so a single-color list gives
# a uniform hatch and a two-color list alternates (hazard-tape style).
STRIPE_PLANNED = "#b0b0b0"          # gray hatch for planned devices
STRIPE_BROKEN = ("#c5221f", "#111111")  # alternating red/black for broken devices
STRIPE_SPACING = 10.0               # px between parallel stripe lines
STRIPE_WIDTH = 2.0
STRIPE_ALPHA = 0.45                 # stripe opacity, so card text stays readable


@dataclass
class IfaceBlock:
    """One interface's three display lines, plus its VLAN colouring."""

    top: str    # "eth0  10.0.1.1/24" (IP omitted when unset)
    mac: str    # "00:00:00:00:00:00"
    vlan: str   # "VLAN 10" or "Trunk: 10,20,30"
    vlan_colors: list[str] = field(default_factory=list)  # chip per VLAN carried
    matches_filter: bool = True  # False = dim; only meaningful with a filter on


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
    status: DeviceStatus = DeviceStatus.ACTIVE
    matches_filter: bool = True   # False when a VLAN filter excludes this device
    # Diagonal-stripe overlay colors, cycled per line by the renderers.
    # Empty = no stripes (ACTIVE). One color = uniform hatch (PLANNED,
    # gray). Two colors = alternating hazard-tape pattern (BROKEN,
    # red/black).
    stripe_colors: list[str] = field(default_factory=list)

    @property
    def striped(self) -> bool:
        """Whether any stripe overlay should be drawn at all."""
        return bool(self.stripe_colors)

    device_model: str = ""
    native_vlan_line: str = ""    # "Native VLAN: 1" — always set (default VLAN 1)
    loopback_line: str = ""       # "Loopback: 10.255.0.1/32", or "" if unset
    config_line: str = ""         # "2 config file(s)", or "" when none attached
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


def build_card(device: Device, vlan_filter: set[int] | None = None) -> NodeCard:
    """Compute the card contents and size for a device.

    `vlan_filter` drives VLAN highlighting: when it is non-empty, every
    interface and the card itself are marked as matching or not, and the
    renderers dim the non-matching ones. Sizing is unaffected, so
    toggling a filter never re-flows the diagram — only its colouring
    changes, which keeps positions stable while a user explores VLANs.
    """
    from .styles import style_for  # local import avoids a cycle at module load
    from .vlans import device_matches_filter, interface_matches_filter, interface_vlans, vlan_color

    style = style_for(device.device_type)

    blocks = []
    for iface in device.interfaces[:MAX_IFACE_BLOCKS]:
        ip = f"  {iface.ip_address}" if iface.ip_address else ""
        carried = sorted(interface_vlans(iface))
        blocks.append(
            IfaceBlock(
                top=f"{iface.name}{ip}",
                mac=iface.mac_address,
                vlan=iface.vlan_summary(),
                # Cap the chips: a trunk allowing 30 VLANs would otherwise
                # draw a stripe wider than the card.
                vlan_colors=[vlan_color(v) for v in carried[:MAX_VLAN_CHIPS]],
                matches_filter=interface_matches_filter(iface, vlan_filter),
            )
        )
    more = max(0, len(device.interfaces) - MAX_IFACE_BLOCKS)

    loopback_line = f"Loopback: {device.loopback_ip}" if device.loopback_ip else ""
    # Surface attachments on the diagram itself; a card that silently hid
    # its configs would make them easy to forget about.
    if device.configs:
        count = len(device.configs)
        config_line = f"{count} config file{'s' if count != 1 else ''} attached"
    else:
        config_line = ""
    native_vlan_line = f"Native VLAN: {device.native_vlan}"
    notes_lines = _wrap_notes(device.notes)

    height = HEADER_H + TYPE_BAND_H + NATIVE_VLAN_H + len(blocks) * IFACE_BLOCK_H + PAD
    if device.device_model:
        height += MODEL_H
    if more:
        height += FOOTER_H
    if loopback_line:
        height += LOOPBACK_H
    if config_line:
        height += CONFIG_H
    if notes_lines:
        height += len(notes_lines) * NOTES_LINE_H + PAD

    # Every status keeps the device-type color scheme; PLANNED and
    # BROKEN differ only in the stripe overlay the renderer draws on
    # top: a uniform gray hatch for planned, alternating red/black
    # (hazard-tape style) for broken.
    if device.status is DeviceStatus.PLANNED:
        stripe_colors = [STRIPE_PLANNED]
    elif device.status is DeviceStatus.BROKEN:
        stripe_colors = list(STRIPE_BROKEN)
    else:
        stripe_colors = []

    return NodeCard(
        width=NODE_W,
        height=height,
        name=device.name,
        type_label=device.device_type.value.replace("_", " "),
        glyph=style.glyph,
        fill=style.fill,
        stroke=style.stroke,
        status=device.status,
        matches_filter=device_matches_filter(device, vlan_filter),
        stripe_colors=stripe_colors,
        device_model=device.device_model,
        native_vlan_line=native_vlan_line,
        loopback_line=loopback_line,
        config_line=config_line,
        iface_blocks=blocks,
        more_count=more,
        notes_lines=notes_lines,
    )
