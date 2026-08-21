"""Import/export plans as portable .netplan JSON files."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from netplanner.domain.entities import Site, Subnet, TextBox, Vlan
from netplanner.domain.model import NetworkPlan
from netplanner.errors import PersistenceError

from .repository import (
    _device_from_dict,
    _device_to_dict,
    _link_from_dict,
    _link_to_dict,
)

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1


def save_project(plan: NetworkPlan, path: Path) -> None:
    """Write a plan to a .netplan JSON file with verbose failure context."""
    logger.info("Exporting plan '%s' (id=%s) to project file %s", plan.name, plan.id, path)
    try:
        _save_project_impl(plan, path)
    except OSError as exc:
        logger.exception("Project file write failed for %s", path)
        raise PersistenceError(
            f"Could not write project file {path} for plan '{plan.name}': "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _save_project_impl(plan: NetworkPlan, path: Path) -> None:
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
        "textboxes": [asdict(t) for t in plan.textboxes.values()],
    }
    path.write_text(json.dumps(doc, indent=2))


def load_project(path: Path) -> NetworkPlan:
    """Read a .netplan JSON file with verbose failure context.

    Distinguishes the three ways this goes wrong — unreadable file,
    invalid JSON, and valid JSON that isn't a plan — because the fix
    for each is different and the message should say which one it is.
    """
    logger.info("Loading project file %s", path)
    try:
        return _load_project_impl(path)
    except OSError as exc:
        logger.exception("Project file unreadable: %s", path)
        raise PersistenceError(
            f"Could not read project file {path}: {type(exc).__name__}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        logger.exception("Project file is not valid JSON: %s", path)
        raise PersistenceError(
            f"Project file {path} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Project file has unexpected structure: %s", path)
        raise PersistenceError(
            f"Project file {path} does not look like a NetPlanner plan "
            f"(missing or malformed field: {exc})"
        ) from exc


def _load_project_impl(path: Path) -> NetworkPlan:
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
    # Files written before annotations existed simply have none.
    for box in doc.get("textboxes", []):
        plan.add_textbox(TextBox(**box))
    for d in doc.get("devices", []):
        plan.add_device(_device_from_dict(d))
    for link in doc.get("links", []):
        plan.add_link(_link_from_dict(link))
    return plan
