"""Per-type styling shared by the GUI canvas and the exporters (lives in
the export layer so exporters never import GUI code).

Keeping device *and* link-media styles in one table guarantees the
on-screen diagram and the PDF/PNG exports stay visually consistent.
"""

from __future__ import annotations

from dataclasses import dataclass

from netplanner.domain.entities import DeviceType, LinkType

# ------------------------------------------------------------------ devices
# The exported page background, and the fill painted behind text box
# annotations. The GUI canvas deliberately keeps the system theme's
# background, so annotations carry their own light panel — exactly like
# device cards do — rather than relying on the surface underneath them.
# That keeps dark annotation text readable on any desktop theme while
# looking identical in the exports.
DIAGRAM_BG = "#ffffff"


@dataclass(frozen=True)
class TypeStyle:
    fill: str         # background color (hex)
    stroke: str       # border color (hex)
    glyph: str        # short unicode glyph drawn on the node
    name_prefix: str  # used for auto-generated device names


STYLES: dict[DeviceType, TypeStyle] = {
    DeviceType.ROUTER:       TypeStyle("#e8f0fe", "#1a56db", "⇄", "rtr"),
    DeviceType.SWITCH:       TypeStyle("#e6f4ea", "#137333", "⇉", "sw"),
    DeviceType.FIREWALL:     TypeStyle("#fce8e6", "#c5221f", "▣", "fw"),
    DeviceType.SERVER:       TypeStyle("#f3e8fd", "#7627bb", "☰", "srv"),
    DeviceType.ACCESS_POINT: TypeStyle("#fef7e0", "#b06000", "📶", "ap"),
    DeviceType.DISH_RADIO:   TypeStyle("#e9eef6", "#3c5a99", "⌔", "dish"),
    DeviceType.AP_RADIO:     TypeStyle("#eef6ee", "#2f6f4f", "⛯", "apr"),
    DeviceType.WORKSTATION:  TypeStyle("#e4f7fb", "#007b83", "🖵", "ws"),
    DeviceType.OTHER:        TypeStyle("#f1f3f4", "#5f6368", "●", "dev"),
}


def style_for(device_type: DeviceType) -> TypeStyle:
    return STYLES.get(device_type, STYLES[DeviceType.OTHER])


def style_for_value(type_value: str) -> TypeStyle:
    """Look up by the enum's string value (used by exporters)."""
    try:
        return style_for(DeviceType(type_value))
    except ValueError:
        return STYLES[DeviceType.OTHER]


# -------------------------------------------------------------------- links
@dataclass(frozen=True)
class LinkStyle:
    color: str                     # line color (hex)
    dash: tuple[int, ...] | None   # dash pattern in px, None = solid
    width: float
    label: str                     # human-readable media name


LINK_STYLES: dict[LinkType, LinkStyle] = {
    LinkType.ETHERNET: LinkStyle("#444444", None,        2.0, "Copper / Ethernet"),
    LinkType.FIBER:    LinkStyle("#e8710a", None,        2.5, "Fiber"),
    LinkType.WIRELESS: LinkStyle("#1a73e8", (6, 5),      2.0, "Wireless"),
    LinkType.SERIAL:   LinkStyle("#9334e6", (2, 4),      2.0, "Serial"),
    LinkType.WAN:      LinkStyle("#c5221f", (10, 4, 2, 4), 2.5, "WAN"),
}


def link_style_for(link_type: LinkType) -> LinkStyle:
    return LINK_STYLES.get(link_type, LINK_STYLES[LinkType.ETHERNET])


def link_style_for_value(type_value: str) -> LinkStyle:
    try:
        return link_style_for(LinkType(type_value))
    except ValueError:
        return LINK_STYLES[LinkType.ETHERNET]
