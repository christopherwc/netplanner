"""Import/export plans as portable .netplan JSON files."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from netplanner.domain.model import NetworkPlan

from .repository import (
    _device_from_dict,
    _device_to_dict,
    _link_from_dict,
    _link_to_dict,
)
from netplanner.domain.entities import Site, Subnet, Vlan

FORMAT_VERSION = 1


def save_project(plan: NetworkPlan, path: Path) -> None:
    doc = {
        "format": "netplan",
        "version": FORMAT_VERSION,
        "id": plan.id,
        "name": plan.name,
        "devices": [_device_to_dict(d) for d in plan.devices],
        "links": [_link_to_dict(link) for link in plan.links],
        "subnets": [asdict(s) for s in plan.subnets.values()],
        "vlans": [asdict(v) for v in plan.vlans.values()],
        "sites": [asdict(s) for s in plan.sites.values()],
    }
    path.write_text(json.dumps(doc, indent=2))


def load_project(path: Path) -> NetworkPlan:
    doc = json.loads(path.read_text())
    if doc.get("format") != "netplan":
        raise ValueError(f"{path} is not a .netplan project file")
    plan = NetworkPlan(name=doc["name"], plan_id=doc.get("id"))
    for s in doc.get("subnets", []):
        plan.add_subnet(Subnet(**s))
    for v in doc.get("vlans", []):
        plan.add_vlan(Vlan(**v))
    for s in doc.get("sites", []):
        plan.add_site(Site(**s))
    for d in doc.get("devices", []):
        plan.add_device(_device_from_dict(d))
    for link in doc.get("links", []):
        plan.add_link(_link_from_dict(link))
    return plan
