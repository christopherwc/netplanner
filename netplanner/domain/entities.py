"""Core domain entities for network plans.

Plain dataclasses, independent of GUI and persistence layers.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv4Network


def new_id() -> str:
    return uuid.uuid4().hex


def random_mac() -> str:
    """Generate a locally-administered unicast MAC (02:xx:xx:xx:xx:xx).

    The 0x02 first octet sets the locally-administered bit and clears
    the multicast bit, so generated addresses can never collide with
    real vendor OUIs.
    """
    octets = [0x02] + [random.randint(0, 255) for _ in range(5)]
    return ":".join(f"{o:02X}" for o in octets)


class DeviceType(Enum):
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    SERVER = "server"
    ACCESS_POINT = "access_point"
    DISH_RADIO = "dish_radio"
    AP_RADIO = "ap_radio"
    WORKSTATION = "workstation"
    OTHER = "other"


class InterfaceType(Enum):
    """Physical/radio interface classes, ordered roughly by speed."""

    WIRELESS = "wireless"
    ETH_1G = "1g"
    ETH_10G = "10g"
    ETH_25G = "25g"
    ETH_100G = "100g"

    @property
    def label(self) -> str:
        """Human-readable label used in menus, dialogs, and labels."""
        return {
            InterfaceType.WIRELESS: "Wireless",
            InterfaceType.ETH_1G: "1 Gbps",
            InterfaceType.ETH_10G: "10 Gbps",
            InterfaceType.ETH_25G: "25 Gbps",
            InterfaceType.ETH_100G: "100 Gbps",
        }[self]


class LinkType(Enum):
    ETHERNET = "ethernet"
    FIBER = "fiber"
    WIRELESS = "wireless"
    SERIAL = "serial"
    WAN = "wan"


@dataclass
class Site:
    """A physical or logical location grouping devices."""

    name: str
    id: str = field(default_factory=new_id)
    notes: str = ""


@dataclass
class Vlan:
    vlan_id: int
    name: str
    id: str = field(default_factory=new_id)


@dataclass
class Subnet:
    cidr: str  # e.g. "10.0.1.0/24"
    name: str = ""
    vlan_id: str | None = None  # references Vlan.id
    id: str = field(default_factory=new_id)

    @property
    def network(self) -> IPv4Network:
        return IPv4Network(self.cidr)


@dataclass
class Interface:
    """A single port on a device.

    Interfaces are referenced by id from Link endpoints, so an interface
    keeps its identity across edits (renaming a port does not detach its
    cable).
    """

    name: str  # e.g. "eth0", "Gig0/1", "wlan0"
    interface_type: InterfaceType = InterfaceType.ETH_1G
    ip_address: str | None = None  # CIDR notation, e.g. "10.0.1.1/24"
    mac_address: str = field(default_factory=random_mac)
    subnet_id: str | None = None  # references Subnet.id
    id: str = field(default_factory=new_id)


@dataclass
class Device:
    name: str
    device_type: DeviceType = DeviceType.OTHER
    site_id: str | None = None
    interfaces: list[Interface] = field(default_factory=list)
    # Canvas position (used by GUI and renderer)
    x: float = 0.0
    y: float = 0.0
    notes: str = ""
    id: str = field(default_factory=new_id)

    def interface_by_name(self, name: str) -> Interface | None:
        return next((i for i in self.interfaces if i.name == name), None)


@dataclass
class Link:
    """A connection between two device interfaces."""

    a_device_id: str
    b_device_id: str
    a_interface_id: str | None = None
    b_interface_id: str | None = None
    link_type: LinkType = LinkType.ETHERNET
    bandwidth_mbps: int | None = None
    label: str = ""
    id: str = field(default_factory=new_id)
