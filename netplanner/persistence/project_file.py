"""Import/export plans as portable .netplan JSON files."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
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

# A .netplan file is the format people mail to each other, so it is the
# one input here that arrives from outside. Two bounds on what it may
# cost to open one: the whole file is read into memory before parsing,
# and json.loads recurses once per level of nesting.
#
# 64 MiB is far past any real plan — a thousand devices with configs
# attached runs to single-digit megabytes — and small enough that a file
# claiming otherwise is refused rather than swapped in.
MAX_PROJECT_BYTES = 64 * 1024 * 1024


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
        "devices": [_device_to_dict(d, include_local_paths=False) for d in plan.devices],
        "links": [_link_to_dict(link) for link in plan.links],
        "subnets": [asdict(s) for s in plan.subnets.values()],
        "vlans": [asdict(v) for v in plan.vlans.values()],
        "sites": [asdict(s) for s in plan.sites.values()],
        "textboxes": [asdict(t) for t in plan.textboxes.values()],
    }
    _write_atomic(path, json.dumps(doc, indent=2))


def _write_atomic(path: Path, text: str) -> None:
    """Write a file so a failure cannot destroy the previous version.

    Overwriting in place truncates the existing file before the new
    content is written, so a full disk halfway through leaves the user
    with neither their old plan nor their new one. Writing a temporary
    file alongside the target and renaming it over the top makes the
    replacement atomic: it either happened or it did not.
    """
    directory = path.parent
    handle, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())  # the rename is only safe once the data is down
        os.replace(tmp_name, path)
    except OSError:
        # Leaving a stray temp file behind would be its own small bug;
        # failing to remove it is not worth masking the real error.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


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
    except UnicodeDecodeError as exc:
        # A JSONDecodeError would be misleading here: the bytes never
        # got as far as the JSON parser. Usually a binary file picked by
        # mistake, or a plan written by a build that did not pin UTF-8.
        logger.exception("Project file is not UTF-8 text: %s", path)
        raise PersistenceError(
            f"Project file {path} is not UTF-8 text (byte {exc.start:#x} at "
            f"offset {exc.start}); it may not be a NetPlanner plan"
        ) from exc
    except json.JSONDecodeError as exc:
        logger.exception("Project file is not valid JSON: %s", path)
        raise PersistenceError(
            f"Project file {path} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    except RecursionError as exc:
        # Nesting deep enough to exhaust the interpreter stack. Python
        # raises this from json.loads before any field is read, and it
        # is a RuntimeError rather than a ValueError, so it would
        # otherwise travel straight past every handler here and out
        # through the UI as an unhandled crash.
        logger.error("Project file nesting is too deep to parse: %s", path)
        raise PersistenceError(
            f"Project file {path} is nested too deeply to parse; it may be "
            f"corrupt or deliberately malformed"
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Project file has unexpected structure: %s", path)
        raise PersistenceError(
            f"Project file {path} does not look like a NetPlanner plan "
            f"(missing or malformed field: {exc})"
        ) from exc


def _load_project_impl(path: Path) -> NetworkPlan:
    size = path.stat().st_size
    if size > MAX_PROJECT_BYTES:
        raise ValueError(
            f"{path} is {size} bytes, over the {MAX_PROJECT_BYTES}-byte limit "
            f"for a project file"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    # Valid JSON that is not an object at all (a list, a bare string)
    # would otherwise fail later with an AttributeError from .get().
    if not isinstance(doc, dict):
        raise TypeError(f"{path} contains a JSON {type(doc).__name__}, not a plan object")
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
