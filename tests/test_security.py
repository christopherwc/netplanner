"""What the application refuses to do with the files it touches.

Two themes. Confidentiality of what it writes: a plan carries device
configs, and a device config carries secrets, so the database and the
log are the owner's business and nobody else's. And bounds on what it
reads: a .netplan file is the format people mail each other, which makes
it the one input here that arrives from outside.
"""

from __future__ import annotations

import json
import logging
import stat

import pytest

from netplanner.app.controller import MAX_CONFIG_BYTES, AppController
from netplanner.domain.entities import ConfigFile, ConfigFormat, Device, DeviceType
from netplanner.domain.model import NetworkPlan
from netplanner.errors import ConfigImportError, PersistenceError
from netplanner.permissions import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    restrict_to_owner,
)
from netplanner.persistence.db import default_db_path, make_engine
from netplanner.persistence.project_file import (
    MAX_PROJECT_BYTES,
    load_project,
    save_project,
)
from netplanner.persistence.repository import PlanRepository


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ------------------------------------------------- confidentiality at rest
def test_the_database_and_its_directory_are_owner_only(tmp_path, monkeypatch):
    """The database holds attached device configs, and those hold enable
    secrets and community strings. Under the default umask it would be
    world-readable on a shared machine."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_path = default_db_path()
    make_engine(db_path).dispose()

    assert _mode(db_path.parent) == PRIVATE_DIR_MODE
    assert _mode(db_path) == PRIVATE_FILE_MODE


def test_a_data_directory_from_an_older_build_is_narrowed(tmp_path, monkeypatch):
    """mkdir's mode applies only when it creates the directory, so a
    directory left behind by a build that predates this has to be
    chmod'ed rather than assumed."""
    stale = tmp_path / "netplanner"
    stale.mkdir(mode=0o755)
    assert _mode(stale) == 0o755

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    default_db_path()
    assert _mode(stale) == PRIVATE_DIR_MODE


def test_the_log_and_its_directory_are_owner_only(tmp_path):
    """The log names every plan, device and path the user touches."""
    from netplanner.log import setup_logging

    log_dir = tmp_path / "logs"
    logger = setup_logging(log_dir=log_dir)
    try:
        assert _mode(log_dir) == PRIVATE_DIR_MODE
        assert _mode(log_dir / "netplanner.log") == PRIVATE_FILE_MODE
    finally:
        # Close, not just detach: the suite runs with warnings as errors,
        # so a leaked file handle fails the run rather than lingering.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_a_filesystem_that_cannot_chmod_warns_instead_of_failing(tmp_path, caplog):
    """A FAT stick or some network mounts cannot honour a mode. Refusing
    to start there would trade confidentiality for total loss of
    function, so the weaker posture is logged rather than assumed."""
    missing = tmp_path / "not-there"
    with caplog.at_level(logging.WARNING, logger="netplanner.permissions"):
        restrict_to_owner(missing, PRIVATE_FILE_MODE)
    assert "readable by other users" in caplog.text


# --------------------------------------------------- bounds on what we read
def test_a_project_file_larger_than_the_limit_is_refused(tmp_path):
    """The whole file is read into memory before it is parsed, so the
    size is checked first rather than discovered."""
    oversized = tmp_path / "big.netplan"
    oversized.write_bytes(b'{"format":"netplan"}' + b" " * (MAX_PROJECT_BYTES + 1))

    with pytest.raises(PersistenceError, match="over the"):
        load_project(oversized)


def test_a_deeply_nested_project_file_is_refused_not_crashed(tmp_path):
    """json.loads recurses once per level and raises RecursionError,
    which is a RuntimeError — it would travel straight past the handlers
    for malformed files and out through the UI as a crash."""
    nested = tmp_path / "deep.netplan"
    nested.write_text(
        '{"format":"netplan","name":"x","devices":' + "[" * 200_000 + "]" * 200_000 + "}"
    )

    with pytest.raises(PersistenceError, match="nested too deeply"):
        load_project(nested)


def test_an_oversized_config_is_refused_before_it_is_read(tmp_path):
    """Config content is held in memory and copied into every undo
    snapshot, so the ceiling sits close to what a real running-config
    costs."""
    huge = tmp_path / "huge.cfg"
    huge.write_bytes(b"x" * (MAX_CONFIG_BYTES + 1))

    with pytest.raises(ConfigImportError, match="over the"):
        AppController.read_config_file(huge)


def test_a_config_at_the_limit_still_imports(tmp_path):
    """The bound is a ceiling, not a hair trigger."""
    ok = tmp_path / "ok.cfg"
    ok.write_bytes(b"hostname core\n")
    assert AppController.read_config_file(ok).content == "hostname core\n"


# ------------------------------------------- what leaves on a shared plan
def _plan_with_imported_config() -> NetworkPlan:
    device = Device(name="core", device_type=DeviceType.SWITCH)
    device.configs.append(
        ConfigFile(
            filename="core.cfg",
            content="hostname core\n",
            config_format=ConfigFormat.CISCO_IOS,
            source_path="/home/chris/clients/acme-bank/core-sw.cfg",
        )
    )
    plan = NetworkPlan("shared")
    plan.add_device(device)
    return plan


def test_an_exported_plan_does_not_carry_the_senders_paths(tmp_path):
    """The import path describes the sender's filesystem and their
    client list, neither of which is part of the network being
    documented."""
    path = tmp_path / "share.netplan"
    save_project(_plan_with_imported_config(), path)

    doc = json.loads(path.read_text())
    assert doc["devices"][0]["configs"][0]["source_path"] is None
    assert "acme-bank" not in path.read_text()


def test_the_config_itself_still_survives_the_export(tmp_path):
    """Stripping the path must not strip the file it pointed at."""
    path = tmp_path / "share.netplan"
    save_project(_plan_with_imported_config(), path)

    config = load_project(path).devices[0].configs[0]
    assert config.content == "hostname core\n"
    assert config.filename == "core.cfg"


def test_the_local_database_keeps_the_path(tmp_path):
    """Locally it is useful — it is what a re-import follows. The
    distinction is between storing it and sending it."""
    repo = PlanRepository(db_path=tmp_path / "local.db")
    plan = _plan_with_imported_config()
    repo.save(plan)

    restored = repo.load(plan.id).devices[0].configs[0]
    assert restored.source_path == "/home/chris/clients/acme-bank/core-sw.cfg"
