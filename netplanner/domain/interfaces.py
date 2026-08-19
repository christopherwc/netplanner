"""Default interface sets per device type (Packet Tracer style).

New devices are created with a realistic set of ports. Users can add
or remove interfaces afterwards via the interface editor.
"""

from __future__ import annotations

from .entities import DeviceType, Interface

_TEMPLATES: dict[DeviceType, list[str]] = {
    DeviceType.ROUTER: ["Gig0/0", "Gig0/1", "Gig0/2", "Gig0/3"],
    DeviceType.SWITCH: [f"Gig0/{n}" for n in range(1, 9)],
    DeviceType.FIREWALL: ["wan0", "lan0", "lan1", "dmz0"],
    DeviceType.SERVER: ["eth0", "eth1"],
    DeviceType.ACCESS_POINT: ["eth0", "wlan0"],
    DeviceType.DISH_RADIO: ["eth0", "wlan0 (PtP)"],
    DeviceType.AP_RADIO: ["eth0", "wlan0 (sector)"],
    DeviceType.WORKSTATION: ["eth0", "wlan0"],
    DeviceType.OTHER: ["eth0"],
}


def default_interfaces(device_type: DeviceType) -> list[Interface]:
    names = _TEMPLATES.get(device_type, _TEMPLATES[DeviceType.OTHER])
    return [Interface(name=name) for name in names]
