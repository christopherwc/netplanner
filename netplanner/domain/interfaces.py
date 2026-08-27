"""Default interface sets per device type (Packet Tracer style).

New devices are created with a realistic set of ports, each stating the
rate it runs at. Users can change the count and the rates afterwards via
the interface editor (right-click a device -> "Edit interfaces...").
"""

from __future__ import annotations

from .entities import DeviceType, Interface

# (name, max_speed_mbps) templates per device type. Switches get 10G
# uplinks in addition to their 1G access ports; radios get a port with
# no rate, because a link's throughput there depends on modulation,
# distance and channel width. Inventing a figure for one would be worse
# than leaving it for whoever ran the site survey to fill in.
_GIG, _TEN_GIG, _UNMEASURED = 1_000, 10_000, None

_TEMPLATES: dict[DeviceType, list[tuple[str, int | None]]] = {
    DeviceType.ROUTER: [(f"Gig0/{n}", _GIG) for n in range(4)],
    DeviceType.SWITCH: (
        [(f"Gig0/{n}", _GIG) for n in range(1, 9)]
        + [("Ten0/1", _TEN_GIG), ("Ten0/2", _TEN_GIG)]
    ),
    DeviceType.FIREWALL: [
        ("wan0", _GIG),
        ("lan0", _GIG),
        ("lan1", _GIG),
        ("dmz0", _GIG),
    ],
    DeviceType.SERVER: [
        ("eth0", _TEN_GIG),
        ("eth1", _TEN_GIG),
    ],
    DeviceType.ACCESS_POINT: [
        ("eth0", _GIG),
        ("wlan0", _UNMEASURED),
    ],
    DeviceType.DISH_RADIO: [
        ("eth0", _GIG),
        ("wlan0 (PtP)", _UNMEASURED),
    ],
    DeviceType.AP_RADIO: [
        ("eth0", _GIG),
        ("wlan0 (sector)", _UNMEASURED),
    ],
    DeviceType.WORKSTATION: [
        ("eth0", _GIG),
        ("wlan0", _UNMEASURED),
    ],
    DeviceType.OTHER: [("eth0", _GIG)],
}


def default_interfaces(device_type: DeviceType) -> list[Interface]:
    """Build the fresh interface list for a newly-placed device."""
    template = _TEMPLATES.get(device_type, _TEMPLATES[DeviceType.OTHER])
    return [Interface(name=name, max_speed_mbps=mbps) for name, mbps in template]
