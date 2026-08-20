"""Tests for the logging system and verbose boundary exceptions.

Uses pytest's caplog for record inspection and tmp_path for the
rotating file handler, so no test touches the real log directory.
"""

import logging

import pytest

from netplanner.domain.entities import DeviceType


@pytest.fixture()
def ctrl():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    return AppController(repository=MagicMock())


# ---------------------------------------------------------------- setup
def test_setup_logging_writes_to_file(tmp_path):
    from netplanner.log import setup_logging

    logger = setup_logging(log_dir=tmp_path)
    logging.getLogger("netplanner.test").info("hello from the test")
    for handler in logger.handlers:
        handler.flush()

    log_file = tmp_path / "netplanner.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert "hello from the test" in content
    assert "INFO" in content
    assert "netplanner.test" in content  # origin module is recorded


def test_setup_logging_is_idempotent(tmp_path):
    """Calling setup twice must not duplicate handlers (= doubled lines)."""
    from netplanner.log import setup_logging

    logger = setup_logging(log_dir=tmp_path)
    first_count = len(logger.handlers)
    logger = setup_logging(log_dir=tmp_path)
    assert len(logger.handlers) == first_count


def test_setup_logging_survives_unwritable_dir(tmp_path):
    """An unusable log directory must degrade to console-only, not raise."""
    from netplanner.log import setup_logging

    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")  # mkdir will fail
    logger = setup_logging(log_dir=blocked / "logs")
    assert logger.handlers  # console handler still present


def test_log_file_path_respects_env(tmp_path, monkeypatch):
    from netplanner.log import log_file_path

    monkeypatch.setenv("NETPLANNER_LOG_DIR", str(tmp_path))
    assert log_file_path() == tmp_path / "netplanner.log"


# ---------------------------------------------------------- controller logs
def test_add_device_is_logged(ctrl, caplog):
    with caplog.at_level(logging.INFO, logger="netplanner"):
        ctrl.add_device("rtr1", DeviceType.ROUTER, 10, 20)
    assert any(
        "rtr1" in record.message and "router" in record.message
        for record in caplog.records
    )


def test_delete_device_logs_cascade_count(ctrl, caplog):
    from netplanner.domain.entities import LinkType

    a = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("sw1", DeviceType.SWITCH, 100, 0)
    ctrl.add_link(a.id, b.id, LinkType.ETHERNET)
    with caplog.at_level(logging.INFO, logger="netplanner"):
        ctrl.delete_device(a.id)
    assert any(
        "rtr1" in r.message and "1 attached link" in r.message for r in caplog.records
    )


def test_command_stack_logs_descriptions(ctrl, caplog):
    with caplog.at_level(logging.DEBUG, logger="netplanner"):
        ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
        ctrl.undo()
        ctrl.redo()
    messages = [r.message for r in caplog.records]
    assert any(m.startswith("Command:") for m in messages)
    assert any(m.startswith("Undo:") for m in messages)
    assert any(m.startswith("Redo:") for m in messages)


# ------------------------------------------------------------- persistence
def test_save_and_load_are_logged(tmp_path, caplog):
    from netplanner.app.controller import AppController
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "log.db")
    ctrl = AppController(repository=repo)
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    with caplog.at_level(logging.INFO, logger="netplanner"):
        ctrl.save()
        ctrl.load(ctrl.plan.id)
    messages = " | ".join(r.message for r in caplog.records)
    assert "Saving plan" in messages
    assert "1 devices" in messages
    assert "Loading plan id=" in messages


def test_load_missing_plan_raises_verbose_persistence_error(tmp_path, caplog):
    from netplanner.errors import PersistenceError
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "log.db")
    with caplog.at_level(logging.INFO, logger="netplanner"):
        with pytest.raises(PersistenceError) as excinfo:
            repo.load("no-such-id")
    # The message alone must identify the id and the database searched.
    assert "no-such-id" in str(excinfo.value)
    assert "log.db" in str(excinfo.value)


def test_corrupt_project_file_error_names_file_and_position(tmp_path):
    from netplanner.errors import PersistenceError
    from netplanner.persistence.project_file import load_project

    bad = tmp_path / "broken.netplan"
    bad.write_text('{"name": "x", INVALID JSON')
    with pytest.raises(PersistenceError) as excinfo:
        load_project(bad)
    message = str(excinfo.value)
    assert "broken.netplan" in message
    assert "line" in message  # JSON position is part of the message


def test_project_file_wrong_structure_is_distinguished(tmp_path):
    """Valid JSON that isn't a plan gets its own message, not a JSON error."""
    from netplanner.errors import PersistenceError
    from netplanner.persistence.project_file import load_project

    not_a_plan = tmp_path / "other.netplan"
    not_a_plan.write_text('{"totally": "unrelated"}')
    with pytest.raises(PersistenceError) as excinfo:
        load_project(not_a_plan)
    assert "does not look like a NetPlanner plan" in str(excinfo.value)


def test_unreadable_project_file_error_names_path(tmp_path):
    from netplanner.errors import PersistenceError
    from netplanner.persistence.project_file import load_project

    with pytest.raises(PersistenceError) as excinfo:
        load_project(tmp_path / "missing.netplan")
    assert "missing.netplan" in str(excinfo.value)


# ------------------------------------------------------------------ export
def test_export_to_unwritable_path_raises_verbose_export_error(ctrl, tmp_path):
    from pathlib import Path

    from netplanner.errors import ExportError

    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    bad_path = tmp_path / "no" / "such" / "dir" / "out.pdf"
    with pytest.raises(ExportError) as excinfo:
        ctrl.export_to_pdf(bad_path)
    message = str(excinfo.value)
    assert "out.pdf" in message
    assert "Untitled plan" in message or "plan" in message


def test_export_logs_plan_and_scene_dimensions(ctrl, tmp_path, caplog):
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    with caplog.at_level(logging.INFO, logger="netplanner"):
        ctrl.export_to_png(tmp_path / "ok.png")
    assert any(
        "PNG" in r.message and "1 devices" in r.message for r in caplog.records
    )


# ------------------------------------------------------------------ layout
def test_layout_fallback_logs_warning(ctrl, caplog, monkeypatch):
    import networkx as nx

    def explode(*args, **kwargs):
        raise ImportError("No module named 'numpy'")

    monkeypatch.setattr(nx, "spring_layout", explode)
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    with caplog.at_level(logging.WARNING, logger="netplanner"):
        ctrl.run_auto_layout()
    assert any(
        "circle fallback" in r.message and "numpy" in r.message
        for r in caplog.records
    )


def test_unknown_layout_algorithm_logs_error(caplog):
    from netplanner.domain.entities import Device
    from netplanner.domain.layout import auto_layout
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("x")
    plan.add_device(Device(name="d0"))
    with caplog.at_level(logging.ERROR, logger="netplanner"):
        with pytest.raises(ValueError):
            auto_layout(plan, "bogus")
    assert any("bogus" in r.message for r in caplog.records)


# ------------------------------------------------------------ config import
def test_config_import_missing_file_raises_verbose_error(tmp_path):
    from netplanner.app.controller import AppController
    from netplanner.errors import ConfigImportError

    with pytest.raises(ConfigImportError) as excinfo:
        AppController.read_config_file(tmp_path / "nope.cfg")
    assert "nope.cfg" in str(excinfo.value)


def test_config_import_logs_detected_format(tmp_path, caplog):
    from netplanner.app.controller import AppController

    path = tmp_path / "r.rsc"
    path.write_text("# by RouterOS\n/interface bridge add name=br0\n")
    with caplog.at_level(logging.INFO, logger="netplanner"):
        AppController.read_config_file(path)
    assert any("mikrotik" in r.message for r in caplog.records)


# --------------------------------------------------------------- exceptions
def test_error_types_chain_their_cause(tmp_path):
    """Every wrapped error must keep the original exception chained, so
    the log's traceback reaches the true origin."""
    from netplanner.errors import PersistenceError
    from netplanner.persistence.project_file import load_project

    try:
        load_project(tmp_path / "absent.netplan")
    except PersistenceError as exc:
        assert isinstance(exc.__cause__, OSError)
    else:
        pytest.fail("expected PersistenceError")


def test_netplanner_error_hierarchy():
    from netplanner.errors import (
        ConfigImportError,
        ExportError,
        NetPlannerError,
        PersistenceError,
    )

    for error_type in (PersistenceError, ExportError, ConfigImportError):
        assert issubclass(error_type, NetPlannerError)
