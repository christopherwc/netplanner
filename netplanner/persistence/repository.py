"""Repository: maps NetworkPlan domain objects to/from the database."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from netplanner.domain.entities import (
    Device,
    DeviceStatus,
    DeviceType,
    Interface,
    InterfaceType,
    Link,
    LinkType,
    Site,
    Subnet,
    Vlan,
    VlanMode,
)
from netplanner.domain.model import NetworkPlan

from .db import DeviceRow, LinkRow, PlanRow, make_session_factory


class PlanRepository:
    def __init__(self, db_path: Path | None = None):
        self._session_factory = make_session_factory(db_path)

    # ------------------------------------------------------------------- save
    def save(self, plan: NetworkPlan) -> None:
        with self._session_factory() as session:
            row = session.get(PlanRow, plan.id) or PlanRow(id=plan.id)
            row.name = plan.name
            row.meta = {
                "subnets": [asdict(s) for s in plan.subnets.values()],
                "vlans": [asdict(v) for v in plan.vlans.values()],
                "sites": [asdict(s) for s in plan.sites.values()],
            }
            row.devices = [
                DeviceRow(id=d.id, payload=_device_to_dict(d)) for d in plan.devices
            ]
            row.links = [
                LinkRow(id=link.id, payload=_link_to_dict(link)) for link in plan.links
            ]
            session.merge(row)
            session.commit()

    # ------------------------------------------------------------------- load
    def load(self, plan_id: str) -> NetworkPlan:
        with self._session_factory() as session:
            row = session.get(PlanRow, plan_id)
            if row is None:
                raise KeyError(f"No plan with id {plan_id}")
            plan = NetworkPlan(name=row.name, plan_id=row.id)
            for meta_subnet in row.meta.get("subnets", []):
                plan.add_subnet(Subnet(**meta_subnet))
            for meta_vlan in row.meta.get("vlans", []):
                plan.add_vlan(Vlan(**meta_vlan))
            for meta_site in row.meta.get("sites", []):
                plan.add_site(Site(**meta_site))
            for d_row in row.devices:
                plan.add_device(_device_from_dict(d_row.payload))
            for l_row in row.links:
                plan.add_link(_link_from_dict(l_row.payload))
            return plan

    def list_plans(self) -> list[tuple[str, str]]:
        """Return (id, name) for every stored plan."""
        with self._session_factory() as session:
            return [(r.id, r.name) for r in session.query(PlanRow).all()]

    def delete(self, plan_id: str) -> None:
        with self._session_factory() as session:
            row = session.get(PlanRow, plan_id)
            if row:
                session.delete(row)
                session.commit()


# ------------------------------------------------------------- serialization
def _device_to_dict(d: Device) -> dict:
    """Serialize a Device (and its interfaces) to a JSON-safe dict."""
    data = asdict(d)
    data["device_type"] = d.device_type.value
    data["status"] = d.status.value
    for iface_data, iface in zip(data["interfaces"], d.interfaces):
        iface_data["interface_type"] = iface.interface_type.value
        iface_data["vlan_mode"] = iface.vlan_mode.value
    return data


def _device_from_dict(data: dict) -> Device:
    """Rebuild a Device from a stored dict.

    Payloads written before interface types existed lack the
    "interface_type" key; those default to 1 Gbps. Payloads written
    before status tags existed lack "status"; those default to Active.
    """
    data = dict(data)
    data["device_type"] = DeviceType(data["device_type"])
    data["status"] = DeviceStatus(data.get("status", "active"))
    data["interfaces"] = [_interface_from_dict(i) for i in data.get("interfaces", [])]
    return Device(**data)


def _interface_from_dict(data: dict) -> Interface:
    """Rebuild an Interface, tolerating pre-MAC, pre-type, and pre-VLAN payloads."""
    data = dict(data)
    data["interface_type"] = InterfaceType(data.get("interface_type", "1g"))
    # Older plans predate VLAN support; default to plain access-mode VLAN 1.
    data["vlan_mode"] = VlanMode(data.get("vlan_mode", "access"))
    data.setdefault("access_vlan", 1)
    data.setdefault("trunk_vlans", [])
    # Older plans predate MACs; omitting the key lets the dataclass
    # default generate a fresh one.
    if not data.get("mac_address"):
        data.pop("mac_address", None)
    return Interface(**data)


def _link_to_dict(link: Link) -> dict:
    data = asdict(link)
    data["link_type"] = link.link_type.value
    return data


def _link_from_dict(data: dict) -> Link:
    data = dict(data)
    data["link_type"] = LinkType(data["link_type"])
    return Link(**data)
