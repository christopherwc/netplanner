"""Per-device-type styling shared by the GUI canvas and the exporters (lives in the export layer so exporters never import GUI code).

Keeping this in one table guarantees the on-screen diagram and the
PDF/PNG exports stay visually consistent.
"""

from __future__ import annotations

from dataclasses import dataclass

from netplanner.domain.entities import DeviceType


@dataclass(frozen=True)
class TypeStyle:
    fill: str        # background color (hex)
    stroke: str      # border color (hex)
    glyph: str       # short unicode glyph drawn on the node
    name_prefix: str # used for auto-generated device names


STYLES: dict[DeviceType, TypeStyle] = {
    DeviceType.ROUTER:       TypeStyle("#e8f0fe", "#1a56db", "⇄", "rtr"),
    DeviceType.SWITCH:       TypeStyle("#e6f4ea", "#137333", "⇉", "sw"),
    DeviceType.FIREWALL:     TypeStyle("#fce8e6", "#c5221f", "▣", "fw"),
    DeviceType.SERVER:       TypeStyle("#f3e8fd", "#7627bb", "☰", "srv"),
    DeviceType.ACCESS_POINT: TypeStyle("#fef7e0", "#b06000", "📶", "ap"),
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
