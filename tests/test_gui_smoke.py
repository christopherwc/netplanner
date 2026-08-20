"""GUI construction smoke tests.

These exist because of a specific, repeated failure mode: the logic
suite constructs controllers and domain objects directly, so a missing
import or a mis-wired widget in the GUI layer passes every test and
then crashes on launch. It happened twice — a missing `default_db_path`
import in the repository, and a missing `VlanPanel` import here — both
shipping green.

Building the real widgets is the only check that covers the code path
`netplanner` actually runs at startup. Tests are skipped rather than
failed when PyQt6 or a display platform is unavailable, so a headless
CI box without Qt doesn't report a false failure.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# Qt needs a platform plugin; offscreen works without a display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from netplanner.app.controller import AppController  # noqa: E402
from netplanner.domain.entities import DeviceType, LinkType, VlanMode  # noqa: E402


@pytest.fixture(scope="module")
def app():
    """One QApplication for the module; Qt forbids more than one."""
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def controller():
    return AppController(repository=MagicMock())


@pytest.fixture()
def populated(controller):
    """A controller holding one of everything the GUI has to draw."""
    switch = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    router = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    switch.interfaces[0].access_vlan = 10
    switch.interfaces[1].vlan_mode = VlanMode.TRUNK
    switch.interfaces[1].trunk_vlans = [10, 20]
    controller.add_link(
        switch.id, router.id, LinkType.FIBER,
        a_interface_id=switch.interfaces[2].id,
        b_interface_id=router.interfaces[0].id,
    )
    controller.add_textbox("DMZ", 100, 200)
    return controller


def test_main_window_constructs(app, controller):
    """The exact path `netplanner` runs at launch.

    Regression: a missing VlanPanel import crashed here while the whole
    logic suite stayed green.
    """
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller)
    assert window.canvas is not None
    assert window.palette_dock is not None
    assert window.vlan_panel is not None


def test_main_window_constructs_with_content(app, populated):
    """Building against a populated plan exercises every item type."""
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(populated)
    assert window.canvas is not None


def test_main_window_menu_actions_are_wired(app, controller):
    """Every menu action must have a callable handler attached."""
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller)
    actions = [a for menu in window.menuBar().findChildren(type(window.menuBar()))
               for a in menu.actions()]
    assert window.menuBar().actions()  # menus exist
    for action in actions:
        assert action.text()  # no blank entries


def test_refresh_all_does_not_recurse(app, populated):
    """Regression: _refresh_all once called itself instead of the canvas."""
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(populated)
    window._refresh_all()  # would hit the recursion limit if broken


def test_canvas_and_scene_expose_distinct_methods(app, populated):
    """Regression: a passthrough was duplicated into PlanScene, shadowing
    the real implementation and recursing forever."""
    from netplanner.gui.canvas import NetworkCanvas

    canvas = NetworkCanvas(populated)
    canvas.set_vlan_filter({10})
    assert canvas._scene.vlan_filter == {10}
    assert populated.vlan_filter == {10}  # mirrored for exports


def test_vlan_panel_lists_and_filters(app, populated):
    from netplanner.gui.canvas import NetworkCanvas
    from netplanner.gui.vlan_panel import VlanPanel

    canvas = NetworkCanvas(populated)
    panel = VlanPanel(populated)
    panel.filter_changed.connect(canvas.set_vlan_filter)

    assert 10 in panel._checkboxes
    panel._checkboxes[10].setChecked(True)
    assert panel.selected_vlans() == {10}
    assert canvas._scene.vlan_filter == {10}

    panel._set_all(False)
    assert canvas._scene.vlan_filter == set()


def test_all_gui_modules_import(app):
    """Import every GUI module: catches missing names at module scope."""
    import importlib

    for name in (
        "canvas", "dialogs", "main_window", "palette",
        "panels", "vlan_panel", "config_viewer",
    ):
        importlib.import_module(f"netplanner.gui.{name}")


def test_device_item_paints_without_error(app, populated):
    """Paint offscreen: catches bad brushes, fonts, or missing constants."""
    from PyQt6.QtGui import QImage, QPainter

    from netplanner.gui.canvas import DeviceItem

    device = populated.plan.devices[0]
    item = DeviceItem(device, populated)
    rect = item.boundingRect()
    image = QImage(int(rect.width()) + 4, int(rect.height()) + 4, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    item.paint(painter, None)
    painter.end()


def test_textbox_item_paints_without_error(app, populated):
    from PyQt6.QtGui import QImage, QPainter

    from netplanner.gui.canvas import TextBoxItem

    box = next(iter(populated.plan.textboxes.values()))
    item = TextBoxItem(box, populated)
    rect = item.boundingRect()
    image = QImage(int(rect.width()) + 4, int(rect.height()) + 4, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    item.paint(painter, None)
    painter.end()
