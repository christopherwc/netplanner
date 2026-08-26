"""Error-path coverage for the boundaries that touch the outside world.

Each test here drives a real failure rather than asserting on a mock:
an unwritable directory, a database row edited behind the app's back, a
file of bytes that are not UTF-8. The point is that every one of them
arrives as a NetPlannerError whose message names what was being done
and to what, because that message is all a user sees in the dialog.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from netplanner.domain.entities import Device, DeviceType
from netplanner.domain.model import NetworkPlan
from netplanner.errors import ConfigImportError, PersistenceError
from netplanner.persistence.db import DeviceRow, PlanRow, default_db_path, make_session_factory
from netplanner.persistence.project_file import load_project, save_project
from netplanner.persistence.repository import PlanRepository


@pytest.fixture(scope="module")
def app():
    """Keep one QApplication alive for the whole module.

    Widgets built without a live QApplication abort the process rather
    than raising, and a QApplication created by another module's fixture
    can be collected once that module finishes.
    """
    pytest.importorskip("PyQt6", reason="PyQt6 not installed")
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


def _plan_with_device() -> tuple[NetworkPlan, Device]:
    plan = NetworkPlan("error paths")
    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    plan.add_device(device)
    return plan, device


# ----------------------------------------------------------- database setup
def test_data_directory_that_cannot_be_created(tmp_path, monkeypatch):
    """A read-only or occupied data path fails before any window exists."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, so mkdir underneath it cannot work")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocker))

    with pytest.raises(PersistenceError) as excinfo:
        default_db_path()
    assert "data directory" in str(excinfo.value)
    assert str(blocker) in str(excinfo.value)


def test_database_that_cannot_be_opened(tmp_path):
    """SQLite reports an unreachable file only when the schema is created."""
    unreachable = tmp_path / "missing-dir" / "plans.db"

    with pytest.raises(PersistenceError) as excinfo:
        make_session_factory(unreachable)
    assert str(unreachable) in str(excinfo.value)


# --------------------------------------------------------------- repository
def test_load_reports_a_payload_it_cannot_understand(tmp_path):
    """A plan written by a newer build must not surface as a bare ValueError.

    Simulated by editing the stored device type to something this
    version has no enum member for, which is exactly what a forward
    format change looks like from here.
    """
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    plan, device = _plan_with_device()
    repo.save(plan)

    with repo._session_factory() as session:
        row = session.get(DeviceRow, device.id)
        payload = dict(row.payload)
        payload["device_type"] = "quantum-toaster"
        row.payload = payload
        session.commit()

    with pytest.raises(PersistenceError) as excinfo:
        repo.load(plan.id)
    message = str(excinfo.value)
    assert plan.id in message
    assert "cannot read" in message
    # The underlying cause stays attached for the log.
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_load_tolerates_a_row_with_no_meta(tmp_path):
    """Rows predating the meta column store NULL, not an empty object."""
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    plan, _ = _plan_with_device()
    repo.save(plan)

    with repo._session_factory() as session:
        row = session.get(PlanRow, plan.id)
        row.meta = None
        session.commit()

    loaded = repo.load(plan.id)
    assert len(loaded.devices) == 1
    assert loaded.sites == {}


def test_list_plans_wraps_database_failure(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    from sqlalchemy.exc import OperationalError

    def boom():
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    repo._session_factory = boom
    with pytest.raises(PersistenceError) as excinfo:
        repo.list_plans()
    assert "list stored plans" in str(excinfo.value)


def test_delete_wraps_database_failure(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    from sqlalchemy.exc import OperationalError

    def boom():
        raise OperationalError("DELETE", {}, Exception("database is locked"))

    repo._session_factory = boom
    with pytest.raises(PersistenceError) as excinfo:
        repo.delete("some-id")
    assert "some-id" in str(excinfo.value)


# ------------------------------------------------------------- project files
def test_saving_leaves_no_temporary_files_behind(tmp_path):
    plan, _ = _plan_with_device()
    path = tmp_path / "plan.netplan"
    save_project(plan, path)
    assert [p.name for p in tmp_path.iterdir()] == ["plan.netplan"]


def test_a_failed_save_does_not_destroy_the_previous_file(tmp_path):
    """The whole point of writing through a temporary file."""
    plan, _ = _plan_with_device()
    path = tmp_path / "plan.netplan"
    save_project(plan, path)
    original = path.read_text(encoding="utf-8")

    plan.name = "second attempt"
    with (
        patch("os.replace", side_effect=OSError("No space left on device")),
        pytest.raises(PersistenceError) as excinfo,
    ):
        save_project(plan, path)

    assert "No space left" in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == original  # untouched
    assert [p.name for p in tmp_path.iterdir()] == ["plan.netplan"]  # temp cleaned up


def test_project_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "binary.netplan"
    path.write_bytes(b"\xff\xfe\x00\x00not text at all")

    with pytest.raises(PersistenceError) as excinfo:
        load_project(path)
    assert "not UTF-8" in str(excinfo.value)


def test_project_file_holding_json_that_is_not_an_object(tmp_path):
    """Valid JSON, wrong shape: previously an AttributeError from .get()."""
    path = tmp_path / "list.netplan"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(PersistenceError) as excinfo:
        load_project(path)
    assert "does not look like a NetPlanner plan" in str(excinfo.value)


def test_project_file_roundtrip_survives_non_ascii(tmp_path):
    """UTF-8 is pinned on both sides, so this no longer depends on locale."""
    plan = NetworkPlan("Zürich ↔ København")
    plan.add_device(Device(name="rtr-café", device_type=DeviceType.ROUTER))
    path = tmp_path / "accents.netplan"
    save_project(plan, path)
    assert load_project(path).name == "Zürich ↔ København"


# ---------------------------------------------------------------- GUI import
def test_config_import_failure_reaches_the_user(app, tmp_path):
    """Regression: the handler caught OSError, but the controller raises
    ConfigImportError, so a failed import escaped uncaught."""
    from PyQt6.QtWidgets import QMessageBox

    from netplanner.gui.dialogs import _ConfigsTab

    tab = _ConfigsTab(Device(name="sw1", device_type=DeviceType.SWITCH))
    missing = str(tmp_path / "gone.cfg")

    with (
        patch(
            "netplanner.gui.dialogs.QFileDialog.getOpenFileNames",
            return_value=([missing], ""),
        ),
        patch(
            "netplanner.app.controller.AppController.read_config_file",
            side_effect=ConfigImportError(f"Could not read config file {missing}"),
        ),
        patch.object(QMessageBox, "warning") as warning,
    ):
        tab._import_configs()

    warning.assert_called_once()
    assert missing in warning.call_args.args[2]
    assert tab.result_configs() == []
    tab.deleteLater()


# ------------------------------------------------------------------ startup
def test_startup_failure_shows_a_dialog_instead_of_a_traceback(app, monkeypatch, tmp_path):
    """An unwritable data directory must not kill the app silently."""
    import PyQt6.QtWidgets as qtw

    import netplanner.main as main_mod

    monkeypatch.setenv("NETPLANNER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored afterwards

    fake_app = MagicMock()
    monkeypatch.setattr(qtw, "QApplication", MagicMock(return_value=fake_app))
    monkeypatch.setattr(
        "netplanner.app.controller.AppController",
        MagicMock(side_effect=PersistenceError("Could not open the database /nowhere")),
    )
    with patch.object(qtw.QMessageBox, "critical") as critical:
        assert main_mod.main() == 1

    critical.assert_called_once()
    assert "Could not open the database" in critical.call_args.args[2]
    fake_app.exec.assert_not_called()  # never reached the event loop


def test_unhandled_exception_is_logged_and_shown(app, caplog):
    """Qt aborts after this hook returns, so it is the only chance to speak."""
    import logging

    import PyQt6.QtWidgets as qtw

    import netplanner.main as main_mod

    try:
        raise RuntimeError("canvas exploded")
    except RuntimeError as exc:
        exc_info = (type(exc), exc, exc.__traceback__)

    with (
        caplog.at_level(logging.CRITICAL, logger="netplanner.main"),
        patch.object(qtw.QMessageBox, "critical") as critical,
    ):
        main_mod._report_unhandled(*exc_info)

    critical.assert_called_once()
    shown = critical.call_args.args[2]
    assert "RuntimeError: canvas exploded" in shown
    assert "netplanner.log" in shown  # points at the file holding the traceback
    assert any("Unhandled exception" in r.message for r in caplog.records)


def test_main_installs_the_excepthook(app, monkeypatch, tmp_path):
    import PyQt6.QtWidgets as qtw

    import netplanner.main as main_mod

    monkeypatch.setenv("NETPLANNER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)

    fake_app = MagicMock()
    fake_app.exec.return_value = 0
    monkeypatch.setattr(qtw, "QApplication", MagicMock(return_value=fake_app))
    assert main_mod.main() == 0
    assert sys.excepthook is main_mod._report_unhandled


# --------------------------------------------------------------- validation
def test_validation_reports_a_malformed_ip_instead_of_raising():
    """Before: ValueError escaped and the Validate action showed a crash
    dialog — the one feature meant to report bad data failed on it."""
    from netplanner.app.validation import Severity, validate
    from netplanner.domain.entities import Interface

    plan = NetworkPlan("typos")
    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="eth0", ip_address="10.0.0.256/24"))
    plan.add_device(device)

    issues = validate(plan)
    bad_ip = [i for i in issues if "unreadable IP" in i.message]
    assert len(bad_ip) == 1
    assert bad_ip[0].severity is Severity.ERROR
    assert bad_ip[0].device_id == device.id


def test_validation_reports_a_malformed_subnet_and_still_compares_the_rest():
    from netplanner.app.validation import validate
    from netplanner.domain.entities import Subnet

    plan = NetworkPlan("subnets")
    plan.add_subnet(Subnet(cidr="not-a-network", name="typo"))
    plan.add_subnet(Subnet(cidr="10.0.0.0/24", name="a"))
    plan.add_subnet(Subnet(cidr="10.0.0.0/25", name="b"))

    messages = [i.message for i in validate(plan)]
    assert any("is not a valid network" in m for m in messages)
    # The bad entry does not stop the good ones being compared.
    assert any("overlap" in m for m in messages)


# ---------------------------------------------------------- resource cleanup
def test_closing_a_repository_releases_its_pooled_connections(tmp_path):
    """The pool keeps SQLite files open until the engine is disposed.

    Left undisposed, those connections are eventually finalized by the
    garbage collector, which on Python 3.13+ reports each one as an
    unclosed database.
    """
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    plan, _ = _plan_with_device()
    repo.save(plan)
    assert repo._engine.pool.checkedin() == 1  # idle, still open

    repo.close()
    assert repo._engine.pool.checkedin() == 0


def test_repository_works_as_a_context_manager(tmp_path):
    plan, _ = _plan_with_device()
    with PlanRepository(db_path=tmp_path / "plans.db") as repo:
        repo.save(plan)
        assert repo.load(plan.id).name == plan.name
    assert repo._engine.pool.checkedin() == 0


def test_controller_close_releases_the_repository():
    repository = MagicMock()
    from netplanner.app.controller import AppController

    AppController(repository=repository).close()
    repository.close.assert_called_once()


def test_a_failed_open_does_not_leak_the_engine(tmp_path):
    """The engine exists before create_all runs, so the error path has
    to dispose it on the way out."""
    from netplanner.persistence import db as db_module

    created = []
    real_create_engine = db_module.create_engine

    def tracking_create_engine(*args, **kwargs):
        engine = real_create_engine(*args, **kwargs)
        created.append(engine)
        return engine

    with (
        patch.object(db_module, "create_engine", tracking_create_engine),
        pytest.raises(PersistenceError),
    ):
        db_module.make_engine(tmp_path / "missing-dir" / "plans.db")

    assert len(created) == 1
    assert created[0].pool.checkedin() == 0
