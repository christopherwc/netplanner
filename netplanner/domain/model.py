"""The NetworkPlan aggregate: devices, links, subnets, VLANs, sites.

Backed by a networkx graph for topology queries; entity objects are
stored as node/edge attributes so the rest of the app works with rich
objects rather than raw graph primitives.
"""

from __future__ import annotations

import networkx as nx

from .entities import Device, Link, Site, Subnet, Vlan, new_id


class NetworkPlan:
    def __init__(self, name: str = "Untitled plan", plan_id: str | None = None):
        self.id = plan_id or new_id()
        self.name = name
        self.graph = nx.MultiGraph()  # MultiGraph: parallel links allowed
        self.subnets: dict[str, Subnet] = {}
        self.vlans: dict[str, Vlan] = {}
        self.sites: dict[str, Site] = {}

    # ------------------------------------------------------------------ devices
    def add_device(self, device: Device) -> Device:
        self.graph.add_node(device.id, device=device)
        return device

    def remove_device(self, device_id: str) -> None:
        if self.graph.has_node(device_id):
            self.graph.remove_node(device_id)  # also removes incident links

    def get_device(self, device_id: str) -> Device | None:
        data = self.graph.nodes.get(device_id)
        return data["device"] if data else None

    @property
    def devices(self) -> list[Device]:
        return [d["device"] for _, d in self.graph.nodes(data=True)]

    # -------------------------------------------------------------------- links
    def add_link(self, link: Link) -> Link:
        if not (self.graph.has_node(link.a_device_id) and self.graph.has_node(link.b_device_id)):
            raise ValueError("Both devices must exist before linking them")
        self.graph.add_edge(link.a_device_id, link.b_device_id, key=link.id, link=link)
        return link

    def remove_link(self, link: Link) -> None:
        if self.graph.has_edge(link.a_device_id, link.b_device_id, key=link.id):
            self.graph.remove_edge(link.a_device_id, link.b_device_id, key=link.id)

    @property
    def links(self) -> list[Link]:
        return [d["link"] for _, _, d in self.graph.edges(data=True)]

    # ---------------------------------------------------- subnets/vlans/sites
    def add_subnet(self, subnet: Subnet) -> Subnet:
        self.subnets[subnet.id] = subnet
        return subnet

    def add_vlan(self, vlan: Vlan) -> Vlan:
        self.vlans[vlan.id] = vlan
        return vlan

    def add_site(self, site: Site) -> Site:
        self.sites[site.id] = site
        return site

    # ------------------------------------------------------------------ queries
    def neighbors(self, device_id: str) -> list[Device]:
        return [self.graph.nodes[n]["device"] for n in self.graph.neighbors(device_id)]

    def isolated_devices(self) -> list[Device]:
        return [self.graph.nodes[n]["device"] for n in nx.isolates(self.graph)]
