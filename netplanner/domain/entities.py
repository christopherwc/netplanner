"""Core domain entities for network plans.

Plain dataclasses, independent of GUI and persistence layers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv4Network


def new_id() -> str:
    """Generate a unique hex id for any entity (device, link, VLAN...)."""
    return uuid.uuid4().hex


def blank_mac() -> str:
    """Default MAC for a freshly created interface: all zeros.

    Left as a placeholder for the user to fill in with a real or
    generated address; all-zeros is unambiguous and never looks like a
    plausible real address, unlike a randomly generated one.
    """
    return "00:00:00:00:00:00"


class DeviceType(Enum):
    """Kinds of equipment available in the palette; each has its own
    color scheme, glyph, auto-name prefix, and default interface set."""

    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    SERVER = "server"
    ACCESS_POINT = "access_point"
    DISH_RADIO = "dish_radio"
    AP_RADIO = "ap_radio"
    WORKSTATION = "workstation"
    OTHER = "other"


class DeviceStatus(Enum):
    """Deployment status of a device, shown as a visual tag on its card."""

    ACTIVE = "active"    # normal type colors, no overlay
    PLANNED = "planned"  # normal type colors + diagonal gray stripe overlay
    BROKEN = "broken"    # normal type colors + alternating red/black stripes

    @property
    def label(self) -> str:
        """Human-readable label for the properties dialog's Status dropdown."""
        return {
            DeviceStatus.ACTIVE: "Active",
            DeviceStatus.PLANNED: "Planned",
            DeviceStatus.BROKEN: "Broken",
        }[self]


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


class VlanMode(Enum):
    """How an interface handles VLAN tagging."""

    ACCESS = "access"  # untagged traffic for a single VLAN
    TRUNK = "trunk"    # tagged traffic for multiple VLANs

    @property
    def label(self) -> str:
        """Human-readable label for the interfaces table's VLAN mode dropdown."""
        return {VlanMode.ACCESS: "Access", VlanMode.TRUNK: "Trunk"}[self]


class LinkType(Enum):
    """Physical media of a connection; each renders with a distinct
    line color and dash pattern (see export.styles.LINK_STYLES)."""

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
    """A named VLAN in the plan-wide catalog (e.g. 10 = "Servers")."""

    vlan_id: int
    name: str
    id: str = field(default_factory=new_id)


@dataclass
class Subnet:
    """An IP subnet, optionally tied to a VLAN from the catalog."""

    cidr: str  # e.g. "10.0.1.0/24"
    name: str = ""
    vlan_id: str | None = None  # references Vlan.id
    id: str = field(default_factory=new_id)

    @property
    def network(self) -> IPv4Network:
        """The parsed network object, for overlap checks and math."""
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
    mac_address: str = field(default_factory=blank_mac)
    subnet_id: str | None = None  # references Subnet.id
    vlan_mode: VlanMode = VlanMode.ACCESS
    access_vlan: int = 1  # VLAN carried untagged when vlan_mode is ACCESS
    trunk_vlans: list[int] = field(default_factory=list)  # tagged VLANs when TRUNK
    id: str = field(default_factory=new_id)

    def vlan_summary(self) -> str:
        """Short human-readable VLAN description for cards and menus."""
        if self.vlan_mode is VlanMode.TRUNK:
            if self.trunk_vlans:
                ids = ",".join(str(v) for v in sorted(self.trunk_vlans))
                return f"Trunk: {ids}"
            return "Trunk: (none)"
        return f"VLAN {self.access_vlan}"


@dataclass
class Device:
    """A piece of equipment on the canvas.

    Rendered as a multi-section card (see export.nodecard) showing its
    name, model, type, native VLAN, loopback, interfaces, and notes;
    its status tag (Active/Planned/Broken) controls the stripe overlay.
    """

    name: str
    device_type: DeviceType = DeviceType.OTHER
    site_id: str | None = None
    interfaces: list[Interface] = field(default_factory=list)
    # Canvas position (used by GUI and renderer)
    x: float = 0.0
    y: float = 0.0
    notes: str = ""  # free-form text shown in its own card section
    device_model: str = ""  # e.g. "Cisco ISR 4331", shown under the name
    loopback_ip: str | None = None  # CIDR, e.g. "10.255.0.1/32"; not tied to a physical interface
    native_vlan: int = 1  # device-wide native/management VLAN, shown on the card
    status: DeviceStatus = DeviceStatus.ACTIVE  # deployment tag, shown on the card
    id: str = field(default_factory=new_id)

    def interface_by_name(self, name: str) -> Interface | None:
        """Find an interface by display name; None if absent."""
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
