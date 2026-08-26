"""Manual per-interface line rates.

The Type column still carries the media class; Speed is what the port
actually runs at, defaulting to the type's nominal rate and overridable
for the cases the presets miss — a 2.5G access port, a licensed radio,
a rate-limited handoff.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from netplanner.domain.entities import (
    Device,
    DeviceType,
    Interface,
    InterfaceType,
    Link,
    LinkType,
    format_speed_mbps,
    negotiated_speed_mbps,
    parse_speed_mbps,
)
from netplanner.domain.model import NetworkPlan
from netplanner.persistence.project_file import load_project, save_project
from netplanner.persistence.repository import PlanRepository, _interface_from_dict


# --------------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),          # blank defers to the interface type
        ("   ", None),
        ("850", 850),        # bare numbers are Mbps
        ("1000", 1_000),
        ("100M", 100),
        ("100 Mbps", 100),
        ("2.5G", 2_500),     # the case that motivated the feature
        ("2.5 Gbps", 2_500),
        ("40 Gb/s", 40_000),
        ("10gbit", 10_000),
        ("0.5G", 500),
    ],
)
def test_speeds_people_actually_type(text, expected):
    assert parse_speed_mbps(text) == expected


@pytest.mark.parametrize("text", ["fast", "G", "1..2", "-5", "0", "0.4M"])
def test_typos_are_rejected_rather_than_guessed(text):
    with pytest.raises(ValueError):
        parse_speed_mbps(text)


def test_formatting_picks_the_readable_unit():
    assert format_speed_mbps(100) == "100 Mbps"
    assert format_speed_mbps(1_000) == "1 Gbps"
    assert format_speed_mbps(2_500) == "2.5 Gbps"
    assert format_speed_mbps(100_000) == "100 Gbps"


# ----------------------------------------------------------------------- model
def test_override_replaces_the_type_rate():
    iface = Interface(name="eth0", interface_type=InterfaceType.ETH_1G)
    assert iface.speed_mbps == 1_000
    assert iface.speed_label == "1 Gbps"

    iface.speed_mbps_override = 2_500
    assert iface.speed_mbps == 2_500
    assert iface.speed_label == "2.5 Gbps"


def test_a_radio_can_be_given_the_rate_it_actually_negotiates():
    """Wireless has no nominal rate, which is exactly why a real figure
    from a site survey is worth recording."""
    radio = Interface(name="wlan0", interface_type=InterfaceType.WIRELESS)
    assert radio.speed_mbps is None
    assert radio.speed_label == "Wireless"

    radio.speed_mbps_override = 200
    assert radio.speed_mbps == 200
    assert radio.speed_label == "200 Mbps"


def test_a_link_runs_at_the_slower_end_including_overrides():
    fast = Interface(name="Gig0/0", interface_type=InterfaceType.ETH_10G)
    throttled = Interface(name="Gig0/1", interface_type=InterfaceType.ETH_10G)
    throttled.speed_mbps_override = 500  # rate-limited handoff
    assert negotiated_speed_mbps(fast, throttled) == 500

    radio = Interface(name="wlan0", interface_type=InterfaceType.WIRELESS)
    port = Interface(name="eth0", interface_type=InterfaceType.ETH_1G)
    assert negotiated_speed_mbps(radio, port) == 1_000  # radio has no figure
    radio.speed_mbps_override = 200
    assert negotiated_speed_mbps(radio, port) == 200  # now it does


def test_auto_tracking_links_follow_a_manual_speed():
    plan = NetworkPlan("throttled")
    a = Device(name="sw1", device_type=DeviceType.SWITCH)
    b = Device(name="rtr1", device_type=DeviceType.ROUTER)
    a.interfaces.append(Interface(name="Gig0/0", interface_type=InterfaceType.ETH_10G))
    b.interfaces.append(Interface(name="Gig0/0", interface_type=InterfaceType.ETH_10G))
    plan.add_device(a)
    plan.add_device(b)
    link = plan.add_link(
        Link(
            a_device_id=a.id, b_device_id=b.id, link_type=LinkType.FIBER,
            a_interface_id=a.interfaces[0].id, b_interface_id=b.interfaces[0].id,
        )
    )
    plan.recompute_auto_link_speeds()
    assert link.bandwidth_mbps == 10_000

    a.interfaces[0].speed_mbps_override = 2_500
    assert plan.recompute_auto_link_speeds() == [link.id]
    assert link.bandwidth_mbps == 2_500


def test_a_hand_entered_link_speed_still_wins():
    """bandwidth_auto off means the user's figure is not to be touched,
    whatever the ports say."""
    plan = NetworkPlan("manual")
    a = Device(name="sw1", device_type=DeviceType.SWITCH)
    b = Device(name="sw2", device_type=DeviceType.SWITCH)
    a.interfaces.append(Interface(name="Gig0/0"))
    b.interfaces.append(Interface(name="Gig0/0"))
    plan.add_device(a)
    plan.add_device(b)
    link = plan.add_link(
        Link(
            a_device_id=a.id, b_device_id=b.id, link_type=LinkType.ETHERNET,
            a_interface_id=a.interfaces[0].id, b_interface_id=b.interfaces[0].id,
        )
    )
    link.bandwidth_mbps = 300
    link.bandwidth_auto = False

    a.interfaces[0].speed_mbps_override = 2_500
    assert plan.recompute_auto_link_speeds() == []
    assert link.bandwidth_mbps == 300


# ----------------------------------------------------------------- persistence
def test_override_survives_the_database(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "speeds.db")
    plan = NetworkPlan("speeds")
    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(
        Interface(name="Gig0/1", interface_type=InterfaceType.ETH_1G, speed_mbps_override=2_500)
    )
    plan.add_device(device)
    repo.save(plan)

    loaded = repo.load(plan.id)
    assert loaded.devices[0].interfaces[0].speed_mbps_override == 2_500
    assert loaded.devices[0].interfaces[0].speed_mbps == 2_500


def test_override_survives_a_project_file(tmp_path):
    plan = NetworkPlan("speeds")
    device = Device(name="ptp1", device_type=DeviceType.DISH_RADIO)
    device.interfaces.append(
        Interface(name="wlan0", interface_type=InterfaceType.WIRELESS, speed_mbps_override=450)
    )
    plan.add_device(device)
    path = tmp_path / "speeds.netplan"
    save_project(plan, path)

    assert load_project(path).devices[0].interfaces[0].speed_mbps_override == 450


def test_payloads_written_before_this_feature_default_to_the_type():
    """Plans saved by an older build simply have no key for it."""
    iface = _interface_from_dict({"name": "Gig0/0", "interface_type": "10g"})
    assert iface.speed_mbps_override is None
    assert iface.speed_mbps == 10_000


# ------------------------------------------------------------------ the widget
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6", reason="PyQt6 not installed")
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


def test_speed_combo_offers_presets_and_accepts_typing(app):
    from netplanner.gui.dialogs import _SpeedCombo

    combo = _SpeedCombo(None, InterfaceType.ETH_1G)
    assert combo.current_mbps() is None  # defaults to the type
    assert combo.itemText(0) == "Default (1 Gbps)"

    combo.setCurrentIndex(combo.findData(10_000))  # a preset
    assert combo.current_mbps() == 10_000

    combo.setEditText("2.5g")  # something the presets do not cover
    assert combo.current_mbps() == 2_500
    combo._normalize()
    assert combo.currentText() == "2.5 Gbps"
    combo.deleteLater()


def test_speed_combo_keeps_the_last_good_value_on_a_typo(app):
    """Reverting is better than silently storing None: the user asked
    for a speed, and a typo should not read as 'use the default'."""
    from netplanner.gui.dialogs import _SpeedCombo

    combo = _SpeedCombo(850, InterfaceType.ETH_1G)
    assert combo.current_mbps() == 850

    combo.setEditText("fat-fingered")
    assert combo.current_mbps() == 850
    combo._normalize()
    assert combo.currentText() == "850 Mbps"
    combo.deleteLater()


def test_speed_combo_default_entry_follows_the_type(app):
    from netplanner.gui.dialogs import _SpeedCombo

    combo = _SpeedCombo(None, InterfaceType.ETH_1G)
    combo.set_default_type(InterfaceType.ETH_100G)
    assert combo.itemText(0) == "Default (100 Gbps)"
    assert combo.current_mbps() is None  # still deferring

    combo.set_default_type(InterfaceType.WIRELESS)
    assert combo.itemText(0) == "Default (no fixed rate)"

    # A row already carrying a manual figure keeps it when the type moves.
    custom = _SpeedCombo(2_500, InterfaceType.ETH_1G)
    custom.set_default_type(InterfaceType.ETH_10G)
    assert custom.current_mbps() == 2_500
    combo.deleteLater()
    custom.deleteLater()


def test_interfaces_tab_round_trips_a_manual_speed(app):
    from netplanner.gui.dialogs import DevicePropertiesDialog, _SpeedCombo

    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="Gig0/1", interface_type=InterfaceType.ETH_1G))
    device.interfaces.append(
        Interface(name="Gig0/2", interface_type=InterfaceType.ETH_1G, speed_mbps_override=2_500)
    )
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    # The existing override is shown as typed text, not a preset index.
    assert tab.table.cellWidget(1, 2).currentText() == "2.5 Gbps"

    # Give the first port a figure the presets do not have.
    speed_combo = tab.table.cellWidget(0, 2)
    assert isinstance(speed_combo, _SpeedCombo)
    speed_combo.setEditText("200M")

    result = tab.result_interfaces()
    assert result[0].speed_mbps_override == 200
    assert result[0].speed_mbps == 200
    assert result[1].speed_mbps_override == 2_500
    assert result[0].id == device.interfaces[0].id  # ids survive the edit
    dialog.deleteLater()


def test_clearing_the_speed_returns_the_port_to_its_type(app):
    from netplanner.gui.dialogs import DevicePropertiesDialog

    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(
        Interface(name="Gig0/1", interface_type=InterfaceType.ETH_10G, speed_mbps_override=2_500)
    )
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    tab.table.cellWidget(0, 2).setEditText("")
    result = tab.result_interfaces()
    assert result[0].speed_mbps_override is None
    assert result[0].speed_mbps == 10_000
    dialog.deleteLater()


def test_editing_a_speed_updates_attached_links_and_undoes_cleanly(app):
    """The end-to-end path: type a rate, OK the dialog, and the links
    hanging off that port follow — then undo puts everything back."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.gui.dialogs import DevicePropertiesDialog

    controller = AppController(repository=MagicMock())
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    for device in (sw, rtr):
        device.interfaces[0].interface_type = InterfaceType.ETH_10G
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
    )
    controller.plan.recompute_auto_link_speeds()
    assert link.bandwidth_mbps == 10_000

    dialog = DevicePropertiesDialog(sw)
    dialog._interfaces.table.cellWidget(0, 2).setEditText("2.5 G")
    controller.edit_device_properties(
        sw.id, sw.device_model, sw.loopback_ip, sw.notes, sw.native_vlan, sw.status,
        dialog._interfaces.result_interfaces(),
    )
    assert controller.plan.get_link(link.id).bandwidth_mbps == 2_500

    controller.undo()
    assert controller.plan.get_link(link.id).bandwidth_mbps == 10_000
    assert controller.plan.get_device(sw.id).interfaces[0].speed_mbps_override is None
    dialog.deleteLater()


# ------------------------------------------------------------- custom types
def test_a_custom_type_name_replaces_the_preset_name_only():
    """The enum stays underneath: a name is a name, and the port keeps
    a rate to fall back on."""
    iface = Interface(name="eth0", interface_type=InterfaceType.ETH_25G)
    iface.type_label_override = "SFP28 DAC"
    assert iface.type_label == "SFP28 DAC"
    assert iface.interface_type is InterfaceType.ETH_25G
    assert iface.speed_mbps == 25_000  # still the preset's rate


def test_port_summary_says_only_what_is_worth_saying():
    preset = Interface(name="Gig0/1", interface_type=InterfaceType.ETH_1G)
    assert preset.port_summary == "1 Gbps"  # not "1 Gbps, 1 Gbps"

    custom = Interface(name="eth0", interface_type=InterfaceType.ETH_25G)
    custom.type_label_override = "SFP28 DAC"
    assert custom.port_summary == "SFP28 DAC, 25 Gbps"

    radio = Interface(name="wlan0", interface_type=InterfaceType.WIRELESS)
    radio.type_label_override = "60 GHz PtP"
    assert radio.port_summary == "60 GHz PtP"  # nothing known about the rate
    radio.speed_mbps_override = 1_800
    assert radio.port_summary == "60 GHz PtP, 1.8 Gbps"


def test_custom_type_survives_the_database(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "types.db")
    plan = NetworkPlan("types")
    device = Device(name="rtr1", device_type=DeviceType.ROUTER)
    device.interfaces.append(
        Interface(
            name="Serial0/0",
            interface_type=InterfaceType.ETH_1G,
            type_label_override="T1 serial",
            speed_mbps_override=2,
        )
    )
    plan.add_device(device)
    repo.save(plan)

    restored = repo.load(plan.id).devices[0].interfaces[0]
    assert restored.type_label == "T1 serial"
    assert restored.speed_mbps == 2
    assert restored.interface_type is InterfaceType.ETH_1G


def test_older_payloads_have_no_custom_type():
    iface = _interface_from_dict({"name": "Gig0/0", "interface_type": "1g"})
    assert iface.type_label_override is None
    assert iface.type_label == "1 Gbps"


def test_type_combo_accepts_a_typed_name_and_keeps_its_preset(app):
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_10G, None)
    assert combo.label_override() is None
    assert combo.base_type() is InterfaceType.ETH_10G

    combo.setEditText("SFP28 DAC")
    assert combo.label_override() == "SFP28 DAC"
    assert combo.base_type() is InterfaceType.ETH_10G  # unchanged underneath
    combo.deleteLater()


def test_typing_a_preset_name_is_not_a_custom_type(app):
    """Otherwise "10 Gbps" would be stored as a label that merely looks
    like the preset, and stop following it."""
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_1G, None)
    combo.setEditText("10 gbps")  # casing should not matter
    assert combo.label_override() is None
    combo.deleteLater()


def test_clearing_a_custom_type_returns_to_the_preset(app):
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_25G, "SFP28 DAC")
    assert combo.currentText() == "SFP28 DAC"

    combo.setEditText("   ")
    assert combo.label_override() is None
    combo._normalize()
    assert combo.currentText() == "25 Gbps"
    combo.deleteLater()


def test_choosing_a_preset_moves_the_speed_default_with_it(app):
    """The Speed column's Default entry has to name the preset the row
    actually falls back to, custom label or not."""
    from netplanner.gui.dialogs import DevicePropertiesDialog

    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="Gig0/1", interface_type=InterfaceType.ETH_1G))
    dialog = DevicePropertiesDialog(device)
    type_combo = dialog._interfaces.table.cellWidget(0, 1)
    speed_combo = dialog._interfaces.table.cellWidget(0, 2)
    assert speed_combo.itemText(0) == "Default (1 Gbps)"

    type_combo.setCurrentIndex(1)  # 10 Gbps
    assert speed_combo.itemText(0) == "Default (10 Gbps)"

    type_combo.setEditText("SFP28 DAC")  # a name, not a new preset
    assert speed_combo.itemText(0) == "Default (10 Gbps)"
    dialog.deleteLater()


def test_interfaces_tab_round_trips_a_custom_type(app):
    from netplanner.gui.dialogs import DevicePropertiesDialog

    device = Device(name="rtr1", device_type=DeviceType.ROUTER)
    device.interfaces.append(Interface(name="Se0/0", interface_type=InterfaceType.ETH_1G))
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    tab.table.cellWidget(0, 1).setEditText("T1 serial")
    tab.table.cellWidget(0, 2).setEditText("1.5M")

    result = tab.result_interfaces()[0]
    assert result.type_label_override == "T1 serial"
    assert result.speed_mbps_override == 2  # 1.5 Mbps rounds to the nearest Mbps
    assert result.interface_type is InterfaceType.ETH_1G
    assert result.id == device.interfaces[0].id
    dialog.deleteLater()
