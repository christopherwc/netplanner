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
class TextBox:
    """A free-floating text annotation placed on the canvas.

    Unlike devices, text boxes are not part of the network topology —
    they carry no ports and no links, and never appear in validation or
    graph queries. They exist purely to label regions of a diagram
    ("DMZ", "Rack 3 — scheduled for replacement Q3"), so they live in a
    plain list on the plan rather than as graph nodes.
    """

    text: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 200.0     # wrap width in canvas units; height follows the text
    font_size: float = 11.0
    bold: bool = False
    color: str = "#1a1a1a"   # hex; the palette default reads as body text
    id: str = field(default_factory=new_id)

    @property
    def display_lines(self) -> list[str]:
        """Text split into rendered lines, honoring explicit newlines.

        Word wrapping is applied per paragraph so a long note wraps at
        the box width, while blank lines the user typed are preserved.
        """
        # 0.55 em is a reasonable average glyph width for the sans faces
        # used by all three renderers; bold runs wider, so allow for it or
        # long bold lines overflow the box on the canvas.
        em_ratio = 0.62 if self.bold else 0.55
        chars_per_line = max(8, int(self.width / (self.font_size * em_ratio)))
        lines: list[str] = []
        for paragraph in self.text.split("\n"):
            if not paragraph.strip():
                lines.append("")
                continue
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if len(candidate) > chars_per_line and current:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                lines.append(current)
        return lines or [""]

    @property
    def height(self) -> float:
        """Rendered height, derived from the wrapped line count."""
        return max(self.font_size * 1.6, len(self.display_lines) * self.font_size * 1.35 + 8)


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


class ConfigFormat(Enum):
    """Vendor/format of a stored configuration file.

    Drives syntax highlighting in the viewer and the comment character
    used when detecting the format from file contents.
    """

    PLAIN_TEXT = "text"
    CISCO_IOS = "cisco_ios"
    UBIQUITI = "ubiquiti"
    MIKROTIK = "mikrotik"

    @property
    def label(self) -> str:
        """Human-readable label for the format dropdown."""
        return {
            ConfigFormat.PLAIN_TEXT: "Plain text",
            ConfigFormat.CISCO_IOS: "Cisco IOS",
            ConfigFormat.UBIQUITI: "Ubiquiti",
            ConfigFormat.MIKROTIK: "MikroTik RouterOS",
        }[self]

    @property
    def comment_prefixes(self) -> tuple[str, ...]:
        """Line prefixes that mark a comment in this format."""
        return {
            ConfigFormat.PLAIN_TEXT: ("#",),
            ConfigFormat.CISCO_IOS: ("!",),
            ConfigFormat.UBIQUITI: ("#",),
            ConfigFormat.MIKROTIK: ("#",),
        }[self]


def detect_config_format(text: str, filename: str = "") -> ConfigFormat:
    """Best-effort guess at a config's vendor from its contents.

    Uses signature lines each vendor emits in exported configs. Falls
    back to PLAIN_TEXT rather than guessing wrong — the user can always
    override the format in the dialog.
    """
    head = "\n".join(text.splitlines()[:80]).lower()

    # MikroTik exports open with "# <date> by RouterOS" and use /path commands.
    if "routeros" in head or "/interface " in head or "/ip address add" in head:
        return ConfigFormat.MIKROTIK
    # Cisco IOS uses ! comments plus these near-universal globals.
    if "version " in head and ("service timestamps" in head or "hostname " in head):
        return ConfigFormat.CISCO_IOS
    if "interface gigabitethernet" in head or "spanning-tree mode" in head:
        return ConfigFormat.CISCO_IOS
    # UniFi/EdgeOS style: brace blocks or "set" commands.
    if "ubnt" in head or "edgeos" in head or head.startswith("set "):
        return ConfigFormat.UBIQUITI
    if filename.endswith(".cfg") and "{" in head and "}" in head:
        return ConfigFormat.UBIQUITI
    return ConfigFormat.PLAIN_TEXT


@dataclass
class ConfigFile:
    """A configuration file stored on a device.

    Content is held in the plan itself rather than as a path reference,
    so a saved plan or exported .netplan travels with its configs and
    stays readable on another machine.
    """

    filename: str  # display name, e.g. "core-sw-running.cfg"
    content: str = ""
    config_format: ConfigFormat = ConfigFormat.PLAIN_TEXT
    source_path: str = ""  # where it was imported from, for reference only
    id: str = field(default_factory=new_id)

    @property
    def line_count(self) -> int:
        """Number of lines, shown in the config list."""
        return len(self.content.splitlines())

    @property
    def size_label(self) -> str:
        """Compact size description for the config list."""
        chars = len(self.content)
        if chars < 1024:
            return f"{chars} B"
        return f"{chars / 1024:.1f} KB"


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
    configs: list[ConfigFile] = field(default_factory=list)  # attached config files
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
