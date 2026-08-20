"""The NetworkPlan aggregate: devices, links, subnets, VLANs, sites.

Backed by a networkx graph for topology queries; entity objects are
stored as node/edge attributes so the rest of the app works with rich
objects rather than raw graph primitives.
"""

from __future__ import annotations

import networkx as nx

from .entities import (
    Device,
    Link,
    Site,
    Subnet,
    TextBox,
    Vlan,
    negotiated_speed_mbps,
    new_id,
)


class NetworkPlan:
    def __init__(self, name: str = "Untitled plan", plan_id: str | None = None):
        self.id = plan_id or new_id()
        self.name = name
        self.graph = nx.MultiGraph()  # MultiGraph: parallel links allowed
        self.subnets: dict[str, Subnet] = {}
        self.vlans: dict[str, Vlan] = {}
        self.sites: dict[str, Site] = {}
        # Annotations are not topology, so they sit beside the graph
        # rather than inside it (see TextBox).
        self.textboxes: dict[str, TextBox] = {}

    # -------------------------------------------------------------- textboxes
    def add_textbox(self, textbox: TextBox) -> TextBox:
        """Add a text annotation; returns it for chaining."""
        self.textboxes[textbox.id] = textbox
        return textbox

    def remove_textbox(self, textbox_id: str) -> None:
        """Remove a text annotation; no-op if it does not exist."""
        self.textboxes.pop(textbox_id, None)

    def get_textbox(self, textbox_id: str) -> TextBox | None:
        """Look up a text annotation by id; None if absent."""
        return self.textboxes.get(textbox_id)

    # ------------------------------------------------------------------ devices
    def add_device(self, device: Device) -> Device:
        """Add a device as a graph node; returns it for chaining."""
        self.graph.add_node(device.id, device=device)
        return device

    def remove_device(self, device_id: str) -> None:
        """Remove a device; networkx also drops its incident links."""
        if self.graph.has_node(device_id):
            self.graph.remove_node(device_id)

    def get_device(self, device_id: str) -> Device | None:
        """Look up a device by id; None if it does not exist."""
        data = self.graph.nodes.get(device_id)
        return data["device"] if data else None

    @property
    def devices(self) -> list[Device]:
        """All devices in insertion order."""
        return [d["device"] for _, d in self.graph.nodes(data=True)]

    # -------------------------------------------------------------------- links
    def add_link(self, link: Link) -> Link:
        """Connect two existing devices.

        The link id doubles as the MultiGraph edge key so parallel links
        between the same pair stay individually addressable.

        Raises:
            ValueError: if either endpoint device is not in the plan.
        """
        if not (self.graph.has_node(link.a_device_id) and self.graph.has_node(link.b_device_id)):
            raise ValueError("Both devices must exist before linking them")
        self.graph.add_edge(link.a_device_id, link.b_device_id, key=link.id, link=link)
        return link

    def interface_for(self, device_id: str, interface_id: str | None):
        """Resolve a (device, interface) id pair to the Interface object."""
        if not interface_id:
            return None
        device = self.get_device(device_id)
        if device is None:
            return None
        return next((i for i in device.interfaces if i.id == interface_id), None)

    def derived_link_speed(self, link: Link) -> int | None:
        """Speed a link would run at, given its interfaces' line rates."""
        return negotiated_speed_mbps(
            self.interface_for(link.a_device_id, link.a_interface_id),
            self.interface_for(link.b_device_id, link.b_interface_id),
        )

    def recompute_auto_link_speeds(self) -> list[str]:
        """Refresh every auto-tracking link's speed from its interfaces.

        Called after an interface edit so that changing a port's type
        updates the links attached to it. Links whose bandwidth was
        entered by hand (bandwidth_auto False) are left untouched.

        Returns the ids of links whose speed actually changed, so
        callers can report or log what moved.
        """
        changed = []
        for link in self.links:
            if not link.bandwidth_auto:
                continue
            derived = self.derived_link_speed(link)
            if derived != link.bandwidth_mbps:
                link.bandwidth_mbps = derived
                changed.append(link.id)
        return changed

    def get_link(self, link_id: str) -> Link | None:
        """Look up a link by id; None if absent.

        Links live as keyed edge attributes, so this scans rather than
        indexing — plans are small enough that a dict mirror would be
        state to keep in sync for no measurable gain.
        """
        return next((link for link in self.links if link.id == link_id), None)

    def remove_link(self, link: Link) -> None:
        if self.graph.has_edge(link.a_device_id, link.b_device_id, key=link.id):
            self.graph.remove_edge(link.a_device_id, link.b_device_id, key=link.id)

    @property
    def links(self) -> list[Link]:
        """All links across all device pairs."""
        return [d["link"] for _, _, d in self.graph.edges(data=True)]

    # ---------------------------------------------------- subnets/vlans/sites
    def add_subnet(self, subnet: Subnet) -> Subnet:
        self.subnets[subnet.id] = subnet
        return subnet

    def add_vlan(self, vlan: Vlan) -> Vlan:
        self.vlans[vlan.id] = vlan
        return vlan

    def get_site(self, site_id: str) -> Site | None:
        """Look up a site by id; None if absent."""
        return self.sites.get(site_id)

    def remove_site(self, site_id: str) -> None:
        """Remove a site; no-op if it does not exist. Devices are untouched."""
        self.sites.pop(site_id, None)

    def devices_in_site(self, site_id: str) -> list[Device]:
        """Devices whose position falls inside the site's box.

        Membership is computed from geometry rather than stored, so
        dragging a device into or out of a site changes what the site
        contains without any extra bookkeeping.
        """
        site = self.sites.get(site_id)
        if site is None:
            return []
        return [d for d in self.devices if site.contains_point(d.x, d.y)]

    def add_site(self, site: Site) -> Site:
        self.sites[site.id] = site
        return site

    # ------------------------------------------------------------------ queries
    def neighbors(self, device_id: str) -> list[Device]:
        """Devices directly linked to the given device."""
        return [self.graph.nodes[n]["device"] for n in self.graph.neighbors(device_id)]

    def isolated_devices(self) -> list[Device]:
        """Devices with no links at all (flagged by the validator)."""
        return [self.graph.nodes[n]["device"] for n in nx.isolates(self.graph)]
