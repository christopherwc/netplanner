"""Dialog and config-viewer coverage.

Dialogs are constructed with real devices/links and their result
getters exercised directly; the static Qt prompts (file pickers,
message boxes, input dialogs) are patched so nothing blocks.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

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
    TextBox,
    VlanMode,
)
from netplanner.gui.config_viewer import (
    ConfigTextView,
    ConfigViewerDialog,
)
from netplanner.gui.dialogs import (
    DevicePropertiesDialog,
    LinkPropertiesDialog,
    SiteDialog,
    TextBoxDialog,
    _format_mbps,
    _parse_vlans,
    _vlans_to_text,
)


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def device():
    device = Device(
        name="sw1",
        device_type=DeviceType.SWITCH,
        device_model="C9300",
        loopback_ip="10.255.0.1/32",
        notes="core switch",
        status=DeviceStatus.PLANNED,
    )
    device.interfaces.append(
        Interface(name="Gig0/0", ip_address="10.0.0.1/24", access_vlan=10)
    )
    trunk = Interface(name="Gig0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20])
    device.interfaces.append(trunk)
    device.configs.append(
        ConfigFile(filename="run.cfg", content="hostname sw1\n! comment\n")
    )
    return device


# ------------------------------------------------------------------ helpers
def test_format_mbps_units():
    assert _format_mbps(500) == "500 Mbps"
    assert _format_mbps(1000) == "1 Gbps"
    assert _format_mbps(2500) == "2.5 Gbps"


def test_parse_vlans_access_and_trunk():
    assert _parse_vlans(VlanMode.ACCESS, "20") == (20, [])
    assert _parse_vlans(VlanMode.ACCESS, "junk") == (1, [])
    assert _parse_vlans(VlanMode.ACCESS, "9999") == (1, [])  # out of range
    assert _parse_vlans(VlanMode.TRUNK, "10, 20, nope, 5000") == (1, [10, 20])


def test_vlans_to_text_roundtrip(device):
    assert _vlans_to_text(device.interfaces[0]) == "10"
    assert _vlans_to_text(device.interfaces[1]) == "10,20"


# ------------------------------------------------- DevicePropertiesDialog
def test_device_dialog_results_reflect_edits(app, device):
    dialog = DevicePropertiesDialog(device)
    dialog._general.model_edit.setText("  ISR4331 ")
    dialog._general.loopback_edit.setText("")
    dialog._general.native_vlan_spin.setValue(99)
    dialog._general.status_combo.setCurrentIndex(2)  # BROKEN
    dialog._general.notes_edit.setPlainText("rework")

    assert dialog.result_device_model() == "ISR4331"
    assert dialog.result_loopback_ip() is None
    assert dialog.result_native_vlan() == 99
    assert dialog.result_status() is DeviceStatus.BROKEN
    assert dialog.result_notes() == "rework"
    assert dialog.result_configs() == device.configs
    dialog.deleteLater()


def test_interfaces_tab_add_remove_and_results(app, device):
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    # Add a fresh row via the same helper the button uses, name it, and
    # confirm it comes back as a brand-new interface.
    tab._append_row(
        "", InterfaceType.ETH_10G, None, None, "", "00:00:00:00:00:00",
        VlanMode.ACCESS, "30", None,
    )
    row = tab.table.rowCount() - 1
    tab.table.item(row, 0).setText("Fib0/1")
    tab.table.item(row, 3).setText("172.16.0.1/30")

    # Blank a name so that row is dropped from the results.
    tab.table.item(1, 0).setText("   ")

    result = tab.result_interfaces()
    names = [i.name for i in result]
    assert "Fib0/1" in names and "Gig0/1" not in names
    kept = next(i for i in result if i.name == "Gig0/0")
    assert kept.id == device.interfaces[0].id  # existing ids survive edits

    # Remove the new row through the selection-driven path.
    tab.table.selectRow(row)
    tab._remove_selected()
    assert tab.table.rowCount() == row
    dialog.deleteLater()


# ------------------------------------------------------------ _ConfigsTab
def test_configs_tab_import_view_rename_export_remove(app, device, tmp_path):
    dialog = DevicePropertiesDialog(device)
    tab = dialog._configs

    # Import a real file through the controller helper.
    good = tmp_path / "router.rsc"
    good.write_text("/interface bridge\nadd name=br0\n")
    with patch(
        "netplanner.gui.dialogs.QFileDialog.getOpenFileNames",
        return_value=([str(good)], ""),
    ):
        tab._import_configs()
    assert any(c.filename == "router.rsc" for c in tab._configs)

    # Failed read via a raw OSError. The controller helper wraps those
    # as ConfigImportError (covered in test_error_handling.py); OSError
    # stays in the handler's except tuple for any caller that reads a
    # file directly, and this keeps that half of the tuple honest.
    with patch(
        "netplanner.gui.dialogs.QFileDialog.getOpenFileNames",
        return_value=([str(tmp_path / "missing.cfg")], ""),
    ), patch(
        "netplanner.app.controller.AppController.read_config_file",
        side_effect=OSError("no such file"),
    ), patch("netplanner.gui.dialogs.QMessageBox.warning") as warn:
        tab._import_configs()
    warn.assert_called_once()

    # Nothing selected: every action is a silent no-op.
    tab.table.clearSelection()
    tab._view_selected()
    tab._rename_selected()
    tab._export_selected()
    tab._remove_selected()

    # View the first config through a non-blocking exec.
    tab.table.selectRow(0)
    with patch("netplanner.gui.config_viewer.ConfigViewerDialog.exec", return_value=0):
        tab._view_selected()

    # Rename accepted, then cancelled.
    with patch(
        "netplanner.gui.dialogs.QInputDialog.getText", return_value=("new.cfg", True)
    ):
        tab.table.selectRow(0)
        tab._rename_selected()
    assert tab._configs[0].filename == "new.cfg"
    with patch(
        "netplanner.gui.dialogs.QInputDialog.getText", return_value=("zzz", False)
    ):
        tab.table.selectRow(0)
        tab._rename_selected()
    assert tab._configs[0].filename == "new.cfg"

    # Export: happy path, cancel, then a write failure.
    out = tmp_path / "copy.cfg"
    with patch(
        "netplanner.gui.dialogs.QFileDialog.getSaveFileName",
        return_value=(str(out), ""),
    ):
        tab.table.selectRow(0)
        tab._export_selected()
    assert out.read_text() == tab._configs[0].content
    with patch(
        "netplanner.gui.dialogs.QFileDialog.getSaveFileName", return_value=("", "")
    ):
        tab._export_selected()  # user cancelled: nothing written
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    with patch(
        "netplanner.gui.dialogs.QFileDialog.getSaveFileName",
        return_value=(str(blocked), ""),
    ), patch("netplanner.gui.dialogs.QMessageBox.warning") as warn:
        tab.table.selectRow(0)
        tab._export_selected()
    warn.assert_called_once()

    # Remove: declined, then confirmed.
    count = len(tab._configs)
    with patch(
        "netplanner.gui.dialogs.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        tab.table.selectRow(0)
        tab._remove_selected()
    assert len(tab._configs) == count
    with patch(
        "netplanner.gui.dialogs.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        tab.table.selectRow(0)
        tab._remove_selected()
    assert len(tab._configs) == count - 1
    dialog.deleteLater()


# ----------------------------------------------------------- TextBoxDialog
def test_textbox_dialog_results(app):
    textbox = TextBox(text="hello", font_size=15, bold=True, color="#137333", width=200)
    dialog = TextBoxDialog(textbox)
    assert dialog.result_text() == "hello"
    assert dialog.result_font_size() == 15.0
    assert dialog.result_bold() is True
    assert dialog.result_color() == "#137333"
    assert dialog.result_width() == 200.0
    dialog.deleteLater()


def test_textbox_dialog_unknown_color_falls_back(app):
    dialog = TextBoxDialog(TextBox(text="x", color="#123456"))
    assert dialog.result_color() == dialog.COLOR_CHOICES[0][1]
    dialog.deleteLater()


# ------------------------------------------------------ LinkPropertiesDialog
def test_link_dialog_basics_and_unit_switching(app):
    link = Link(a_device_id="a", b_device_id="b", label="uplink", bandwidth_mbps=500)
    dialog = LinkPropertiesDialog(link, endpoints="sw1 Gig0/0  ↔  rtr1 Gig0/0")
    assert dialog.result_label() == "uplink"
    assert dialog.result_link_type() is LinkType.ETHERNET
    assert dialog.result_bandwidth() == 500
    assert dialog.result_bandwidth_auto() is False

    # Mbps → Gbps keeps the value, not the number.
    dialog.unit_combo.setCurrentIndex(1)
    assert dialog.bandwidth_spin.value() == pytest.approx(0.5)
    assert dialog.result_bandwidth() == 500
    dialog.unit_combo.setCurrentIndex(0)
    assert dialog.result_bandwidth() == 500
    dialog.deleteLater()


def test_link_dialog_gbps_default_for_fast_links(app):
    link = Link(a_device_id="a", b_device_id="b", bandwidth_mbps=10_000)
    dialog = LinkPropertiesDialog(link)
    assert dialog.unit_combo.currentIndex() == 1  # opens in Gbps
    assert dialog.result_bandwidth() == 10_000
    dialog.deleteLater()


def test_link_dialog_auto_tracking(app):
    link = Link(a_device_id="a", b_device_id="b", bandwidth_auto=True)
    dialog = LinkPropertiesDialog(link, derived_speed_mbps=1000)
    assert dialog.auto_check.isChecked()
    assert not dialog.bandwidth_spin.isEnabled()  # locked while tracking
    assert dialog.result_bandwidth() == 1000
    assert dialog.result_bandwidth_auto() is True

    # Untick: field unlocks; typing keeps tracking off.
    dialog.auto_check.setChecked(False)
    assert dialog.bandwidth_spin.isEnabled()
    dialog.unit_combo.setCurrentIndex(0)  # back to Mbps before typing
    dialog.bandwidth_spin.setValue(250)
    assert dialog.result_bandwidth() == 250
    assert dialog.result_bandwidth_auto() is False

    # Re-tick then type a figure by hand: tracking unticks itself.
    dialog.auto_check.setChecked(True)
    dialog.auto_check.setChecked(False)
    dialog.auto_check.setChecked(True)
    dialog.bandwidth_spin.setEnabled(True)  # simulate an editable field
    dialog.bandwidth_spin.setValue(123)
    assert dialog.auto_check.isChecked() is False
    dialog.deleteLater()


def test_link_dialog_without_derived_speed(app):
    link = Link(a_device_id="a", b_device_id="b", bandwidth_auto=True)
    dialog = LinkPropertiesDialog(link, derived_speed_mbps=None)
    assert not dialog.auto_check.isEnabled()
    assert not dialog.auto_check.isChecked()  # auto flag ignored with no source
    assert dialog.result_bandwidth() is None  # spinbox at "not set"
    dialog.deleteLater()


def test_link_dialog_fiber_preselected(app):
    link = Link(a_device_id="a", b_device_id="b", link_type=LinkType.FIBER)
    dialog = LinkPropertiesDialog(link)
    assert dialog.result_link_type() is LinkType.FIBER
    dialog.deleteLater()


# --------------------------------------------------------------- SiteDialog
def test_site_dialog_results(app):
    site = Site(name="IDF 1", notes="rack 12", color="#7627bb")
    dialog = SiteDialog(site, contained=3)
    assert dialog.result_name() == "IDF 1"
    assert dialog.result_notes() == "rack 12"
    assert dialog.result_color() == "#7627bb"
    dialog.deleteLater()


def test_site_dialog_unknown_color_falls_back(app):
    dialog = SiteDialog(Site(name="x", color="#000001"))
    assert dialog.result_color() == SiteDialog.COLOR_CHOICES[0][1]
    dialog.deleteLater()


# ------------------------------------------------------------ config viewer
IOS_SAMPLE = (
    "! saved config\n"
    "version 15.2\n"
    "hostname sw1\n"
    "interface GigabitEthernet0/1\n"
    " ip address 10.0.0.1 255.255.255.0\n"
    ' description "uplink to core"\n'
)

MIKROTIK_SAMPLE = (
    "# RouterOS export\n"
    "/interface bridge\n"
    "add name=br0\n"
    "set 0 comment=\"lan bridge\" address=192.168.88.1/24\n"
)


@pytest.mark.parametrize(
    ("fmt", "content"),
    [
        (ConfigFormat.CISCO_IOS, IOS_SAMPLE),
        (ConfigFormat.MIKROTIK, MIKROTIK_SAMPLE),
        (ConfigFormat.PLAIN_TEXT, "# note\njust text 10.0.0.1\n"),
    ],
)
def test_config_text_view_highlights_each_format(app, fmt, content):
    config = ConfigFile(filename="f.cfg", content=content, config_format=fmt)
    view = ConfigTextView(config)
    view.resize(400, 300)  # triggers resizeEvent + gutter geometry
    # Paint the gutter directly: offscreen widgets never get exposed.
    event = QPaintEvent(QRect(0, 0, view.gutter_width(), 300))
    view.paint_gutter(event)
    assert view.gutter_width() > 0
    view.deleteLater()


def test_config_viewer_dialog_find(app):
    config = ConfigFile(filename="run.cfg", content=IOS_SAMPLE)
    dialog = ConfigViewerDialog(config, "sw1")

    dialog._find_next()  # empty needle: no-op
    dialog.search_edit.setText("hostname")
    dialog._find_next()  # first hit
    assert dialog.status.text() == ""
    dialog._find_next()  # wraps back to the top
    assert "Wrapped" in dialog.status.text()
    dialog.search_edit.setText("zzz-not-here")
    dialog._find_next()
    assert "No match" in dialog.status.text()
    dialog.deleteLater()


def test_highlighter_runs_on_every_format(app):
    """Force a synchronous rehighlight: offscreen widgets defer the
    first highlight pass, so highlightBlock() never runs unprompted."""
    samples = {
        ConfigFormat.CISCO_IOS: IOS_SAMPLE,
        ConfigFormat.MIKROTIK: MIKROTIK_SAMPLE,
        ConfigFormat.UBIQUITI: 'firewall {\n  name "wan-in" # note\n}\n',
        ConfigFormat.PLAIN_TEXT: "# comment\nplain 10.0.0.1/24 text\n",
    }
    for fmt, content in samples.items():
        config = ConfigFile(filename="f.cfg", content=content, config_format=fmt)
        view = ConfigTextView(config)
        view._highlighter.rehighlight()  # runs highlightBlock per line
        view.deleteLater()


def test_gutter_size_hint_and_paint_event(app):
    config = ConfigFile(filename="f.cfg", content="one\ntwo\nthree\n")
    view = ConfigTextView(config)
    gutter = view._gutter
    assert gutter.sizeHint().width() == view.gutter_width()
    gutter.paintEvent(QPaintEvent(QRect(0, 0, gutter.width() or 30, 60)))
    view.deleteLater()


def test_gutter_scroll_and_resize_paths(app):
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QResizeEvent

    config = ConfigFile(filename="f.cfg", content="line\n" * 50)
    view = ConfigTextView(config)
    view._on_update_request(QRect(0, 0, 30, 40), 0)   # repaint branch
    view._on_update_request(QRect(0, 0, 30, 40), 12)  # scroll branch
    view.resizeEvent(QResizeEvent(QSize(400, 300), QSize(200, 150)))
    assert view._gutter.geometry().width() == view.gutter_width()
    view.deleteLater()
