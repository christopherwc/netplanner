"""VLAN color assignment and membership queries.

Lives in the export layer, like styles.py, so the canvas, the PDF
exporter and the PNG exporter all colour a given VLAN identically — a
VLAN's colour is a property of the diagram, not of one renderer.

Colours are derived from the VLAN id rather than from the order VLANs
happen to appear in a plan. That means VLAN 20 is the same colour today,
tomorrow, in a colleague's copy of the plan, and in an exported PDF; an
order-based assignment would silently recolour the whole diagram as soon
as someone deleted a device.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netplanner.domain.entities import VlanMode
from netplanner.domain.model import NetworkPlan

# Distinguishable hues that stay legible as a small chip and as text.
# Deliberately avoids the device-type fills and link colours so a VLAN
# chip is never confused with a media type.
VLAN_PALETTE: tuple[str, ...] = (
    "#1a73e8",  # blue
    "#e8710a",  # orange
    "#137333",  # green
    "#c5221f",  # red
    "#7627bb",  # purple
    "#00838f",  # teal
    "#b06000",  # brown
    "#c2185b",  # pink
    "#5f6368",  # gray
    "#33691e",  # olive
    "#4527a0",  # indigo
    "#00695c",  # dark teal
)

# Drawn when a filter is active and an element is not a member: light
# enough to recede, dark enough to still read as text.
MUTED_COLOR = "#c8c8cc"
MUTED_TEXT = "#9aa0a6"


def vlan_color(vlan_id: int) -> str:
    """Stable colour for a VLAN id.

    Uses modulo over the palette, so ids beyond the palette length wrap.
    Collisions are possible in plans with more than len(VLAN_PALETTE)
    VLANs; the legend always shows the id next to the swatch, so a
    repeat is visible rather than misleading.
    """
    return VLAN_PALETTE[vlan_id % len(VLAN_PALETTE)]


@dataclass
class VlanUsage:
    """Where one VLAN appears across a plan."""

    vlan_id: int
    name: str = ""                                   # from the plan's VLAN catalog
    access_interfaces: int = 0                       # ports carrying it untagged
    trunk_interfaces: int = 0                        # trunks allowing it
    native_on: list[str] = field(default_factory=list)   # device names
    device_names: list[str] = field(default_factory=list)

    @property
    def interface_count(self) -> int:
        return self.access_interfaces + self.trunk_interfaces

    @property
    def device_count(self) -> int:
        return len(self.device_names)

    @property
    def color(self) -> str:
        return vlan_color(self.vlan_id)

    @property
    def label(self) -> str:
        """'20 — Servers' when named, otherwise 'VLAN 20'."""
        return f"{self.vlan_id} — {self.name}" if self.name else f"VLAN {self.vlan_id}"

    @property
    def summary(self) -> str:
        """One-line description for the legend's second row."""
        parts = []
        if self.access_interfaces:
            parts.append(f"{self.access_interfaces} access")
        if self.trunk_interfaces:
            parts.append(f"{self.trunk_interfaces} trunk")
        if self.native_on:
            parts.append(f"native on {len(self.native_on)}")
        detail = ", ".join(parts) if parts else "unused"
        return f"{self.device_count} device(s) · {detail}"


def interface_vlans(interface) -> set[int]:
    """Every VLAN an interface carries, regardless of its mode."""
    if interface.vlan_mode is VlanMode.TRUNK:
        return set(interface.trunk_vlans)
    return {interface.access_vlan}


def device_vlans(device) -> set[int]:
    """Every VLAN a device touches, including its native VLAN."""
    vlans = {device.native_vlan}
    for interface in device.interfaces:
        vlans |= interface_vlans(interface)
    return vlans


def plan_vlan_usage(plan: NetworkPlan) -> list[VlanUsage]:
    """Collect every VLAN in use across the plan, ascending by id.

    Scans interface membership and device native VLANs, so a VLAN shows
    up here whether or not it was ever added to the plan's VLAN catalog.
    Names come from the catalog when present.
    """
    names = {v.vlan_id: v.name for v in plan.vlans.values()}
    usage: dict[int, VlanUsage] = {}

    def entry(vlan_id: int) -> VlanUsage:
        if vlan_id not in usage:
            usage[vlan_id] = VlanUsage(vlan_id=vlan_id, name=names.get(vlan_id, ""))
        return usage[vlan_id]

    for device in plan.devices:
        native = entry(device.native_vlan)
        native.native_on.append(device.name)
        if device.name not in native.device_names:
            native.device_names.append(device.name)

        for interface in device.interfaces:
            if interface.vlan_mode is VlanMode.TRUNK:
                for vlan_id in interface.trunk_vlans:
                    record = entry(vlan_id)
                    record.trunk_interfaces += 1
                    if device.name not in record.device_names:
                        record.device_names.append(device.name)
            else:
                record = entry(interface.access_vlan)
                record.access_interfaces += 1
                if device.name not in record.device_names:
                    record.device_names.append(device.name)

    # Catalog VLANs with no members still deserve a legend row, so a user
    # can see a VLAN they defined but never assigned.
    for vlan_id, name in names.items():
        entry(vlan_id).name = name

    return [usage[vlan_id] for vlan_id in sorted(usage)]


def device_matches_filter(device, vlan_filter: set[int] | None) -> bool:
    """Whether a device touches any VLAN in the active filter."""
    if not vlan_filter:
        return True
    return bool(device_vlans(device) & vlan_filter)


def interface_matches_filter(interface, vlan_filter: set[int] | None) -> bool:
    """Whether an interface carries any VLAN in the active filter."""
    if not vlan_filter:
        return True
    return bool(interface_vlans(interface) & vlan_filter)
