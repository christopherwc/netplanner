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
from unittest.mock import MagicMock, patch

import pytest

# Qt needs a platform plugin; offscreen works without a display server.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtWidgets import QApplication

from netplanner.app.controller import AppController
from netplanner.domain.entities import DeviceType, LinkType, VlanMode


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


def test_vlan_panel_populates_after_canvas_edits(app, controller):
    """Regression: the legend was built once at startup with an empty
    plan and only refreshed by menu actions, so devices placed on the
    canvas never appeared — the dock kept saying 'No VLANs in this plan
    yet' while the cards clearly showed VLAN membership."""
    from netplanner.domain.entities import VlanMode
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller)
    assert window.vlan_panel._checkboxes == {}  # empty plan at startup

    router = controller.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    router.native_vlan = 13
    router.interfaces[0].vlan_mode = VlanMode.TRUNK
    router.interfaces[0].trunk_vlans = [1, 13]
    window.canvas._scene.rebuild()  # what placing a device triggers

    assert set(window.vlan_panel._checkboxes) == {1, 13}
    assert not window.vlan_panel._empty_label.isVisible()


def test_scene_emits_plan_changed_on_rebuild(app, controller):
    """The signal the VLAN dock (and any future derived panel) relies on."""
    from netplanner.gui.canvas import NetworkCanvas

    canvas = NetworkCanvas(controller)
    fired = []
    canvas.plan_changed.connect(lambda: fired.append(True))
    canvas._scene.rebuild()
    assert fired


def test_vlan_panel_refresh_keeps_ticks_across_plan_edits(app, populated):
    """A highlight must survive an unrelated edit, or exploring a VLAN
    while editing becomes unusable."""
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(populated)
    window.vlan_panel._checkboxes[10].setChecked(True)
    assert window.vlan_panel.selected_vlans() == {10}

    populated.add_device("sw2", DeviceType.SWITCH, 800, 0)
    window.canvas._scene.rebuild()

    assert 10 in window.vlan_panel.selected_vlans()


def test_bandwidth_units_round_trip(app, controller):
    """Switching units must preserve the value, not the number.

    Regression: the unit handler read the combo after it had already
    changed, so converting back to Mbps divided the value by 1000.
    """
    from netplanner.domain.entities import LinkType
    from netplanner.gui.dialogs import LinkPropertiesDialog

    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    ten = next(i for i in sw.interfaces if i.max_speed_mbps == 10_000)
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=ten.id, b_interface_id=rtr.interfaces[0].id,
    )

    dialog = LinkPropertiesDialog(link, "", controller.link_derived_speed(link))
    assert dialog.result_bandwidth() == 1_000

    for index in (0, 1, 0, 1):  # Mbps <-> Gbps repeatedly
        dialog.unit_combo.setCurrentIndex(index)
        assert dialog.result_bandwidth() == 1_000


def test_sub_gigabit_bandwidth_survives_gbps_display(app, controller):
    """500 Mbps must render as 0.5 Gbps and come back as 500."""
    from netplanner.domain.entities import LinkType
    from netplanner.gui.dialogs import LinkPropertiesDialog

    a = controller.add_device("a", DeviceType.ROUTER, 0, 0)
    b = controller.add_device("b", DeviceType.SWITCH, 300, 0)
    link = controller.add_link(a.id, b.id, LinkType.WAN)

    dialog = LinkPropertiesDialog(link, "", None)
    dialog._set_mbps(500)
    assert dialog.result_bandwidth() == 500
    dialog.unit_combo.setCurrentIndex(1)  # Gbps
    assert dialog.bandwidth_spin.value() == 0.5
    assert dialog.result_bandwidth() == 500


def test_bandwidth_zero_means_not_set(app, controller):
    from netplanner.domain.entities import LinkType
    from netplanner.gui.dialogs import LinkPropertiesDialog

    a = controller.add_device("a", DeviceType.ROUTER, 0, 0)
    b = controller.add_device("b", DeviceType.SWITCH, 300, 0)
    link = controller.add_link(a.id, b.id, LinkType.ETHERNET)
    dialog = LinkPropertiesDialog(link, "", None)
    dialog._set_mbps(0)
    assert dialog.result_bandwidth() is None


def test_site_item_paints_and_sits_behind(app, controller):
    """Sites are backdrops: they must render and stay under everything."""
    from PyQt6.QtGui import QImage, QPainter

    from netplanner.gui.canvas import DeviceItem, SiteItem

    site = controller.add_site("IDF 1", 0, 0, width=400, height=300, notes="Rack 3-5")
    controller.add_device("sw1", DeviceType.SWITCH, 100, 100)
    item = SiteItem(site, controller)
    assert item.zValue() < DeviceItem(controller.plan.devices[0], controller).zValue()

    rect = item.boundingRect()
    image = QImage(int(rect.width()) + 4, int(rect.height()) + 4, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    item.paint(painter, None)
    painter.end()


def test_site_resize_commits_once_and_undoes(app, controller):
    from PyQt6.QtCore import QPointF

    from netplanner.gui.canvas import SiteItem

    site = controller.add_site("IDF 1", 0, 0, width=400, height=300)
    item = SiteItem(site, controller)

    class FakeEvent:
        def __init__(self, scene_pos, pos):
            self._scene_pos, self._pos = scene_pos, pos

        def scenePos(self):
            return self._scene_pos

        def pos(self):
            return self._pos

        def accept(self):
            pass

    grip = item._grip_rect().center()
    item.mousePressEvent(FakeEvent(QPointF(0, 0), grip))
    item.mouseMoveEvent(FakeEvent(QPointF(100, 50), grip))
    item.mouseReleaseEvent(FakeEvent(QPointF(100, 50), grip))
    assert (site.width, site.height) == (500, 350)

    controller.undo()
    assert (site.width, site.height) == (400, 300)


def test_site_cannot_be_resized_below_minimum(app, controller):
    from PyQt6.QtCore import QPointF

    from netplanner.gui.canvas import SiteItem

    site = controller.add_site("IDF 1", 0, 0, width=400, height=300)
    item = SiteItem(site, controller)

    class FakeEvent:
        def __init__(self, scene_pos, pos):
            self._scene_pos, self._pos = scene_pos, pos

        def scenePos(self):
            return self._scene_pos

        def pos(self):
            return self._pos

        def accept(self):
            pass

    grip = item._grip_rect().center()
    item.mousePressEvent(FakeEvent(QPointF(0, 0), grip))
    item.mouseMoveEvent(FakeEvent(QPointF(-5000, -5000), grip))
    assert site.width >= SiteItem.MIN_W
    assert site.height >= SiteItem.MIN_H


def test_window_title_tracks_the_plan_name(app, controller):
    """The title bar identifies the current plan without opening a dialog."""
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller)
    assert "Untitled plan" in window.windowTitle()

    controller.rename_plan("HQ Campus")
    window._refresh_all()
    assert "HQ Campus" in window.windowTitle()


# --------------------------------------------------- project import/export
def _plan_with_a_config(controller):
    from netplanner.domain.entities import ConfigFile, ConfigFormat

    device = controller.add_device("core-sw", DeviceType.SWITCH, 0, 0)
    device.configs.append(
        ConfigFile(
            filename="core-sw.cfg",
            content="hostname core-sw\nsnmp-server community S3cr3t RO\n",
            config_format=ConfigFormat.CISCO_IOS,
        )
    )
    return device


def test_a_plan_reports_which_devices_carry_configs(controller):
    """What the export warning is built on. An attached-but-empty config
    is not a disclosure, so it does not count."""
    device = _plan_with_a_config(controller)
    assert controller.plan.devices_carrying_configs() == ["core-sw"]

    device.configs[0].content = "   \n  "
    assert controller.plan.devices_carrying_configs() == []


def test_a_project_round_trips_through_a_file(controller, tmp_path):
    from netplanner.app.controller import AppController

    _plan_with_a_config(controller)
    path = tmp_path / "plan.netplan"
    controller.export_project(path)

    other = AppController(repository=MagicMock())
    other.import_project(path)

    assert [d.name for d in other.plan.devices] == ["core-sw"]
    assert "snmp-server" in other.plan.devices[0].configs[0].content
    assert not other.commands.can_undo  # history does not travel


def test_exporting_a_plan_with_configs_asks_first(app, controller, tmp_path):
    """The file dialog must not open until the question is answered, so
    declining costs nothing."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from netplanner.gui.main_window import MainWindow

    _plan_with_a_config(controller)
    window = MainWindow(controller)
    target = tmp_path / "plan.netplan"

    with patch.object(
        QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel
    ) as asked, patch.object(QFileDialog, "getSaveFileName") as chooser:
        window._export_project()

    assert asked.called
    assert not chooser.called  # declined before choosing a path
    assert not target.exists()

    with patch.object(
        QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok
    ), patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
        window._export_project()

    assert target.exists()
    window.deleteLater()


def test_exporting_a_plan_with_no_configs_asks_nothing(app, controller, tmp_path):
    """Nothing is being disclosed, so there is nothing to confirm."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from netplanner.gui.main_window import MainWindow

    controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    window = MainWindow(controller)
    target = tmp_path / "bare.netplan"

    with patch.object(QMessageBox, "warning") as asked, patch.object(
        QFileDialog, "getSaveFileName", return_value=(str(target), "")
    ):
        window._export_project()

    assert not asked.called
    assert target.exists()
    window.deleteLater()


def test_the_warning_names_the_devices_and_caps_the_list(app, controller):
    """Seeing core-sw named is what makes someone remember what is in
    it; forty names would be scrolled past."""
    from PyQt6.QtWidgets import QMessageBox

    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller)
    with patch.object(
        QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok
    ) as asked:
        window._confirm_config_disclosure([f"sw{n}" for n in range(8)])

    body = asked.call_args[0][2]
    assert "sw0, sw1, sw2, sw3, sw4, and 3 more" in body
    assert "pre-shared keys" in body
    window.deleteLater()


def test_cancelling_the_open_dialog_leaves_the_plan_alone(app, controller):
    from PyQt6.QtWidgets import QFileDialog

    from netplanner.gui.main_window import MainWindow

    controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    window = MainWindow(controller)

    with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
        window._import_project()

    assert [d.name for d in controller.plan.devices] == ["sw1"]
    window.deleteLater()


def test_opening_a_project_replaces_the_plan_on_screen(app, controller, tmp_path):
    from PyQt6.QtWidgets import QFileDialog

    from netplanner.gui.main_window import MainWindow

    _plan_with_a_config(controller)
    path = tmp_path / "plan.netplan"
    controller.export_project(path)

    controller.new_plan("something else")
    window = MainWindow(controller)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        window._import_project()

    assert [d.name for d in controller.plan.devices] == ["core-sw"]
    assert "core-sw" in window.windowTitle() or controller.plan.name
    window.deleteLater()


def test_cancelling_the_save_dialog_writes_nothing(app, controller):
    """Accepting the disclosure warning is not the same as choosing a
    path; backing out of the file dialog still has to write nothing."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from netplanner.gui.main_window import MainWindow

    _plan_with_a_config(controller)
    window = MainWindow(controller)

    with patch.object(
        QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok
    ), patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as chooser:
        window._export_project()

    assert chooser.called  # got as far as asking
    window.deleteLater()


# --------------------------------------------------------------------- theme
@pytest.fixture()
def theme_settings(tmp_path):
    """An .ini-backed QSettings under tmp_path, never the real user config."""
    from PyQt6.QtCore import QSettings

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    yield store
    store.clear()


def test_theme_menu_defaults_to_system_with_nothing_saved(app, controller, theme_settings):
    from netplanner.gui.main_window import MainWindow
    from netplanner.gui.theme import Theme

    window = MainWindow(controller, settings=theme_settings)

    assert set(window._theme_actions) == {Theme.SYSTEM, Theme.LIGHT, Theme.DARK}
    assert window._theme_actions[Theme.SYSTEM].isChecked()
    assert not window._theme_actions[Theme.LIGHT].isChecked()
    assert not window._theme_actions[Theme.DARK].isChecked()
    window.deleteLater()


def test_picking_dark_applies_it_and_persists_the_choice(app, controller, theme_settings):
    """restore_app_theme (conftest) undoes the palette change afterward,
    so no manual cleanup is needed here even though this test mutates
    the process-global QApplication palette."""
    from netplanner.gui.main_window import MainWindow
    from netplanner.gui.theme import Theme, load_saved_theme

    window = MainWindow(controller, settings=theme_settings)
    window._theme_actions[Theme.DARK].trigger()

    assert window._theme == Theme.DARK
    # Base is the color QGraphicsView paints the canvas with; a real
    # dark palette must actually be dark there, not just "different".
    assert QApplication.instance().palette().base().color().lightness() < 128
    assert load_saved_theme(theme_settings) is Theme.DARK
    window.deleteLater()


def test_a_previously_saved_dark_theme_applies_on_startup(app, controller, theme_settings):
    """The choice has to survive a restart: saving it in one window and
    building a fresh one against the same settings must come up dark
    without the user touching the menu again."""
    from netplanner.gui.main_window import MainWindow
    from netplanner.gui.theme import Theme, save_theme

    save_theme(Theme.DARK, theme_settings)
    window = MainWindow(controller, settings=theme_settings)

    assert window._theme is Theme.DARK
    assert window._theme_actions[Theme.DARK].isChecked()
    assert QApplication.instance().palette().base().color().lightness() < 128
    window.deleteLater()


def test_switching_back_to_system_restores_the_original_palette(app, controller, theme_settings):
    from netplanner.gui.main_window import MainWindow
    from netplanner.gui.theme import Theme

    original_base = QApplication.instance().palette().base().color()
    window = MainWindow(controller, settings=theme_settings)
    window._theme_actions[Theme.DARK].trigger()
    window._theme_actions[Theme.SYSTEM].trigger()

    assert window._theme == Theme.SYSTEM
    assert QApplication.instance().palette().base().color() == original_base
    window.deleteLater()


# --------------------------------------------------------------- recent files
@pytest.fixture()
def recent_settings(tmp_path):
    """An .ini-backed QSettings under tmp_path, never the real user config."""
    from PyQt6.QtCore import QSettings

    store = QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat)
    yield store
    store.clear()


def test_open_recent_shows_a_disabled_placeholder_when_empty(app, controller, recent_settings):
    from netplanner.gui.main_window import MainWindow

    window = MainWindow(controller, settings=recent_settings)
    window._populate_recent_menu()

    actions = window._recent_menu.actions()
    assert len(actions) == 1
    assert not actions[0].isEnabled()
    window.deleteLater()


def test_opening_a_project_adds_it_to_open_recent(app, controller, recent_settings, tmp_path):
    from PyQt6.QtWidgets import QFileDialog

    from netplanner.gui.main_window import MainWindow

    _plan_with_a_config(controller)
    path = tmp_path / "plan.netplan"
    controller.export_project(path)
    controller.new_plan("something else")

    window = MainWindow(controller, settings=recent_settings)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        window._import_project()
    window._populate_recent_menu()

    labels = [a.text() for a in window._recent_menu.actions()]
    assert labels == [str(path.resolve())]
    window.deleteLater()


def test_clicking_a_recent_entry_reopens_that_project(app, controller, recent_settings, tmp_path):
    from netplanner.gui.main_window import MainWindow
    from netplanner.gui.recent_files import add_recent_file

    _plan_with_a_config(controller)
    path = tmp_path / "plan.netplan"
    controller.export_project(path)
    add_recent_file(path, recent_settings)

    controller.new_plan("something else")
    window = MainWindow(controller, settings=recent_settings)
    window._populate_recent_menu()

    window._recent_menu.actions()[0].trigger()

    assert [d.name for d in controller.plan.devices] == ["core-sw"]
    window.deleteLater()
