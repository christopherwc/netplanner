"""End-to-end workflow integration coverage.

Every individual handler already has focused coverage elsewhere:
test_coverage_main.py drives MainWindow's menu handlers, test_coverage_
canvas.py and test_coverage_dialogs.py drive the canvas and dialogs, and
test_entities.py round-trips every field type through a real SQLite
PlanRepository at the domain layer. All of those, GUI-side, build their
AppController with repository=MagicMock() -- appropriate, since they're
each testing one layer in isolation.

What none of them exercise is the seam between those layers: a real,
on-disk PlanRepository wired into a real MainWindow, driven only through
its public handlers, across a save -> export -> new plan -> import
cycle. A wiring bug there (MainWindow building its own controller
instead of using the injected one, a handler that quietly drops the
repository reference) would pass every mocked-repository test and every
domain-level persistence test while still being broken in the running
app -- which is exactly the failure mode test_gui_smoke.py's docstring
describes for construction-time wiring, one layer up.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtWidgets import QApplication, QMessageBox

from netplanner.app.controller import AppController
from netplanner.domain.entities import ConfigFile, ConfigFormat, DeviceType, LinkType, VlanMode
from netplanner.gui.main_window import MainWindow
from netplanner.persistence.repository import PlanRepository


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def test_plan_survives_save_export_and_reimport_through_a_real_database(app, tmp_path):
    """Build a plan through the real MainWindow against a real SQLite
    file, then round-trip it through window._save(), window._export_
    project(), a fresh plan, and window._import_project(). Confirms the
    data both matches what was built and is genuinely durable, via a
    second, independent repository handle onto the same database file.
    """
    db_path = tmp_path / "plans.db"
    repository = PlanRepository(db_path=db_path)
    controller = AppController(repository=repository)
    window = MainWindow(controller)

    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    sw.interfaces[0].vlan_mode = VlanMode.TRUNK
    sw.interfaces[0].trunk_vlans = [10, 20]
    sw.configs.append(
        ConfigFile(
            filename="sw1.cfg", content="hostname sw1\n", config_format=ConfigFormat.CISCO_IOS,
        )
    )
    controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
        label="Core uplink",
    )

    window._save()
    reader = PlanRepository(db_path=db_path)
    assert controller.plan.id in {pid for pid, _ in reader.list_plans()}
    reader.close()

    project_path = tmp_path / "lab.netplan"
    with patch(
        "netplanner.gui.main_window.QMessageBox.warning",
        return_value=QMessageBox.StandardButton.Ok,
    ), patch(
        "netplanner.gui.main_window.QFileDialog.getSaveFileName",
        return_value=(str(project_path), ""),
    ):
        window._export_project()
    assert project_path.exists()

    window._new_plan()
    assert controller.plan.devices == []

    with patch(
        "netplanner.gui.main_window.QFileDialog.getOpenFileName",
        return_value=(str(project_path), ""),
    ):
        window._import_project()

    reopened = {d.name: d for d in controller.plan.devices}
    assert set(reopened) == {"sw1", "rtr1"}
    assert reopened["sw1"].interfaces[0].vlan_mode is VlanMode.TRUNK
    assert reopened["sw1"].interfaces[0].trunk_vlans == [10, 20]
    assert reopened["sw1"].configs[0].content == "hostname sw1\n"
    link = controller.plan.links[0]
    assert link.label == "Core uplink"
    assert link.link_type is LinkType.FIBER

    # import_project() writes the imported plan straight to the database
    # too (see AppController.import_project) -- confirm that landed for
    # real, through a repository handle this test didn't touch itself.
    reader = PlanRepository(db_path=db_path)
    assert controller.plan.id in {pid for pid, _ in reader.list_plans()}
    reader.close()

    window.close()
    window.deleteLater()
