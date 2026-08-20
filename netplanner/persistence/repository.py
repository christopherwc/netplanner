"""Repository: maps NetworkPlan domain objects to/from the database."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import logging

from sqlalchemy.exc import SQLAlchemyError

from netplanner.errors import PersistenceError
from netplanner.domain.entities import (
    ConfigFile,
    ConfigFormat,
    Device,
    DeviceStatus,
    DeviceType,
    Interface,
    InterfaceType,
    Link,
    LinkType,
    Site,
    Subnet,
    TextBox,
    Vlan,
    VlanMode,
)
from netplanner.domain.model import NetworkPlan

from .db import DeviceRow, LinkRow, PlanRow, default_db_path, make_session_factory


logger = logging.getLogger(__name__)


class PlanRepository:
    def __init__(self, db_path: Path | None = None):
        # Kept for log/error messages: every PersistenceError names the
        # database it was talking to.
        self.db_path = db_path or default_db_path()
        self._session_factory = make_session_factory(db_path)
        logger.debug("Repository opened at %s", self.db_path)

    # ------------------------------------------------------------------- save
    def _describe(self, plan: NetworkPlan) -> str:
        """One-line description of a plan for log/error messages."""
        return (
            f"plan '{plan.name}' (id={plan.id}, {len(plan.devices)} devices, "
            f"{len(plan.links)} links)"
        )

    def save(self, plan: NetworkPlan) -> None:
        """Persist a plan (upsert by id), with verbose failure context.

        Failures are logged with the full traceback and re-raised as
        PersistenceError naming the plan and database path, so the
        error is diagnosable from the message alone.
        """
        logger.info("Saving %s to %s", self._describe(plan), self.db_path)
        try:
            self._save_impl(plan)
        except (SQLAlchemyError, OSError) as exc:
            logger.exception("Save failed for %s", self._describe(plan))
            raise PersistenceError(
                f"Could not save {self._describe(plan)} to {self.db_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        logger.debug("Save complete for plan id=%s", plan.id)

    def _save_impl(self, plan: NetworkPlan) -> None:
        """The actual upsert; separated so save() stays a guarded shell."""
        with self._session_factory() as session:
            row = session.get(PlanRow, plan.id) or PlanRow(id=plan.id)
            row.name = plan.name
            row.meta = {
                "subnets": [asdict(s) for s in plan.subnets.values()],
                "vlans": [asdict(v) for v in plan.vlans.values()],
                "sites": [asdict(s) for s in plan.sites.values()],
                "textboxes": [asdict(t) for t in plan.textboxes.values()],
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
        """Load a plan by id, with verbose failure context.

        A missing id raises PersistenceError (not a bare KeyError) so
        callers and the GUI guard get a human-readable message naming
        the id and database it was expected in.
        """
        logger.info("Loading plan id=%s from %s", plan_id, self.db_path)
        try:
            plan = self._load_impl(plan_id)
        except PersistenceError:
            raise  # already contextual; don't double-wrap
        except (SQLAlchemyError, OSError) as exc:
            logger.exception("Load failed for plan id=%s", plan_id)
            raise PersistenceError(
                f"Could not load plan id={plan_id} from {self.db_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        logger.debug("Loaded %s", self._describe(plan))
        return plan

    def _load_impl(self, plan_id: str) -> NetworkPlan:
        """The actual query + reconstruction behind load()."""
        with self._session_factory() as session:
            row = session.get(PlanRow, plan_id)
            if row is None:
                raise PersistenceError(
                    f"No plan with id {plan_id} exists in {self.db_path}"
                )
            plan = NetworkPlan(name=row.name, plan_id=row.id)
            for meta_subnet in row.meta.get("subnets", []):
                plan.add_subnet(Subnet(**meta_subnet))
            for meta_vlan in row.meta.get("vlans", []):
                plan.add_vlan(Vlan(**meta_vlan))
            for meta_site in row.meta.get("sites", []):
                plan.add_site(Site(**meta_site))
            # Plans saved before annotations existed simply have none.
            for meta_box in row.meta.get("textboxes", []):
                plan.add_textbox(TextBox(**meta_box))
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
    for cfg_data, cfg in zip(data.get("configs", []), d.configs):
        cfg_data["config_format"] = cfg.config_format.value
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
    # Plans saved before config attachments existed simply have none.
    data["configs"] = [_config_from_dict(c) for c in data.get("configs", [])]
    return Device(**data)


def _config_from_dict(data: dict) -> ConfigFile:
    """Rebuild a ConfigFile, defaulting unknown/absent formats to plain text."""
    data = dict(data)
    data["config_format"] = ConfigFormat(data.get("config_format", "text"))
    return ConfigFile(**data)


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
    """Rebuild a Link, tolerating payloads written before auto speeds."""
    data = dict(data)
    data["link_type"] = LinkType(data["link_type"])
    # Plans saved before speed auto-tracking existed: treat an already
    # recorded bandwidth as deliberate rather than silently starting to
    # overwrite it, and let links with no figure begin tracking.
    data.setdefault("bandwidth_auto", data.get("bandwidth_mbps") is None)
    return Link(**data)
