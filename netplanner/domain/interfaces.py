"""Default interface sets per device type (Packet Tracer style).

New devices are created with a realistic set of typed ports. Users can
change the count and mix afterwards via the interface editor
(right-click a device -> "Edit interfaces...").
"""

from __future__ import annotations

from .entities import DeviceType, Interface, InterfaceType

# (name, type) templates per device type. Switches get 10G uplinks in
# addition to their 1G access ports; radios get wireless ports.
_TEMPLATES: dict[DeviceType, list[tuple[str, InterfaceType]]] = {
    DeviceType.ROUTER: [(f"Gig0/{n}", InterfaceType.ETH_1G) for n in range(4)],
    DeviceType.SWITCH: (
        [(f"Gig0/{n}", InterfaceType.ETH_1G) for n in range(1, 9)]
        + [("Ten0/1", InterfaceType.ETH_10G), ("Ten0/2", InterfaceType.ETH_10G)]
    ),
    DeviceType.FIREWALL: [
        ("wan0", InterfaceType.ETH_1G),
        ("lan0", InterfaceType.ETH_1G),
        ("lan1", InterfaceType.ETH_1G),
        ("dmz0", InterfaceType.ETH_1G),
    ],
    DeviceType.SERVER: [
        ("eth0", InterfaceType.ETH_10G),
        ("eth1", InterfaceType.ETH_10G),
    ],
    DeviceType.ACCESS_POINT: [
        ("eth0", InterfaceType.ETH_1G),
        ("wlan0", InterfaceType.WIRELESS),
    ],
    DeviceType.DISH_RADIO: [
        ("eth0", InterfaceType.ETH_1G),
        ("wlan0 (PtP)", InterfaceType.WIRELESS),
    ],
    DeviceType.AP_RADIO: [
        ("eth0", InterfaceType.ETH_1G),
        ("wlan0 (sector)", InterfaceType.WIRELESS),
    ],
    DeviceType.WORKSTATION: [
        ("eth0", InterfaceType.ETH_1G),
        ("wlan0", InterfaceType.WIRELESS),
    ],
    DeviceType.OTHER: [("eth0", InterfaceType.ETH_1G)],
}


def default_interfaces(device_type: DeviceType) -> list[Interface]:
    """Build the fresh interface list for a newly-placed device.

    The template's type seeds the port's maximum rate as well as its
    media name, so a fresh Gig0/1 arrives stating 1 Gbps outright
    rather than leaving it to be inferred later. Wireless seeds nothing:
    a radio's rate depends on modulation, distance and channel width, so
    the port starts with its rate unknown and waits for a real figure.
    """
    template = _TEMPLATES.get(device_type, _TEMPLATES[DeviceType.OTHER])
    return [
        Interface(name=name, interface_type=itype, max_speed_mbps=itype.speed_mbps)
        for name, itype in template
    ]
