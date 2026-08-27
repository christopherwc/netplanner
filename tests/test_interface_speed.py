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

from PyQt6.QtCore import Qt

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


def test_a_typed_preset_name_is_that_preset_not_a_custom_label(app):
    """Regression. Making the Type column editable meant a preset name
    could arrive without the dropdown being used — typed, completed
    inline by Qt, or pasted — and reading the type from the selected
    index left the port on its old media class while the cell displayed
    the new one."""
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_1G, None)
    combo.setEditText("10 Gbps")
    assert combo.base_type() is InterfaceType.ETH_10G
    assert combo.label_override() is None

    combo.setEditText("10 GBPS")  # casing is not the user's problem
    assert combo.base_type() is InterfaceType.ETH_10G

    combo.setEditText("SFP28 DAC")  # a real custom name still is one
    assert combo.base_type() is InterfaceType.ETH_1G
    assert combo.label_override() == "SFP28 DAC"
    combo.deleteLater()


def test_a_typed_type_change_still_moves_attached_link_speeds(app):
    """The symptom this regression actually produced: retype both ends
    to 10 Gbps and the link stayed at 1 Gbps."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.gui.dialogs import DevicePropertiesDialog

    controller = AppController(repository=MagicMock())
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
    )
    controller.plan.recompute_auto_link_speeds()
    assert link.bandwidth_mbps == 1_000

    for device in (sw, rtr):
        dialog = DevicePropertiesDialog(device)
        dialog._interfaces.table.cellWidget(0, 1).setEditText("10 Gbps")
        controller.edit_device_properties(
            device.id, device.device_model, device.loopback_ip, device.notes,
            device.native_vlan, device.status, dialog._interfaces.result_interfaces(),
        )
        dialog.deleteLater()

    assert controller.plan.get_link(link.id).bandwidth_mbps == 10_000
    controller.undo()
    controller.undo()
    assert controller.plan.get_link(link.id).bandwidth_mbps == 1_000
    assert controller.plan.get_device(sw.id).interfaces[0].interface_type is InterfaceType.ETH_1G


def test_clearing_a_typed_type_returns_to_the_last_selected_preset(app):
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_25G, None)
    combo.setEditText("some scratch text")
    combo.setEditText("")
    combo._normalize()
    assert combo.currentText() == "25 Gbps"
    assert combo.base_type() is InterfaceType.ETH_25G
    combo.deleteLater()


# ------------------------------------------------------------ units of measure
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2.5G", 2_500),
        ("40 Gbps", 40_000),
        ("10GBASE-LR", 10_000),   # the rate is inside the media name
        ("SFP28 25G", 25_000),
        ("100M", 100),
        ("SFP28", None),          # a name, not a rate
        ("T1 serial", None),
        ("DOCSIS 3.1", None),     # 3.1 with no unit is a version, not a speed
        ("", None),
    ],
)
def test_speed_read_out_of_a_typed_interface_type(text, expected):
    from netplanner.domain.entities import speed_from_type_label

    assert speed_from_type_label(text) == expected


def test_two_typed_rates_negotiate_to_the_slower_end(app):
    """End to end: the link speed is the negotiation of the two ports."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.gui.dialogs import DevicePropertiesDialog

    controller = AppController(repository=MagicMock())
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
    )
    controller.plan.recompute_auto_link_speeds()
    assert link.bandwidth_mbps == 1_000

    for device, typed in ((sw, "2.5G"), (rtr, "40G")):
        dialog = DevicePropertiesDialog(device)
        # No focus change: straight from typing to OK, which is what a
        # person does and what editingFinished alone would have missed.
        dialog._interfaces.table.cellWidget(0, 1).setEditText(typed)
        controller.edit_device_properties(
            device.id, device.device_model, device.loopback_ip, device.notes,
            device.native_vlan, device.status, dialog._interfaces.result_interfaces(),
        )
        dialog.deleteLater()

    assert controller.plan.get_link(link.id).bandwidth_mbps == 2_500  # the slower end
    controller.undo()
    controller.undo()
    assert controller.plan.get_link(link.id).bandwidth_mbps == 1_000


def test_a_preset_type_does_not_become_a_manual_speed(app):
    """Picking 10 Gbps from the list means the port follows the preset,
    not that it carries a hand-set figure that stops tracking."""
    from netplanner.gui.dialogs import DevicePropertiesDialog

    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="Gig0/1", interface_type=InterfaceType.ETH_1G))
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    tab.table.cellWidget(0, 1).setCurrentIndex(1)  # 10 Gbps preset
    result = tab.result_interfaces()[0]
    assert result.interface_type is InterfaceType.ETH_10G
    assert result.speed_mbps_override is None
    assert result.speed_mbps == 10_000
    dialog.deleteLater()


# ------------------------------------------- the derived Negotiated column
def _dialog_for(controller, device):
    from netplanner.gui.dialogs import DevicePropertiesDialog

    return DevicePropertiesDialog(device, controller.plan.peer_speeds_for(device))


def _linked_pair(a_type=InterfaceType.ETH_1G, b_type=InterfaceType.ETH_1G):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    controller = AppController(repository=MagicMock())
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    sw.interfaces[0].interface_type = a_type
    rtr.interfaces[0].interface_type = b_type
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
    )
    controller.plan.recompute_auto_link_speeds()
    return controller, sw, rtr, link


def test_negotiated_column_is_capped_by_the_far_end(app):
    """The column's whole point: 40G into a 10G port is 10G."""
    controller, sw, _, _ = _linked_pair(b_type=InterfaceType.ETH_10G)
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    assert table.item(0, 2).text() == "1"  # 1G port, 10G peer
    assert table.cellWidget(0, 3).currentText() == "Gbps"

    table.cellWidget(0, 1).setEditText("40G")
    assert table.item(0, 2).text() == "10"  # the peer is the limit
    assert table.cellWidget(0, 3).currentText() == "Gbps"
    dialog.deleteLater()


def test_negotiated_column_is_not_editable(app):
    controller, sw, _, _ = _linked_pair()
    dialog = _dialog_for(controller, sw)
    item = dialog._interfaces.table.item(0, 2)
    assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
    dialog.deleteLater()


def test_an_unpatched_port_shows_its_own_rate(app):
    """With nothing on the far end there is nothing to negotiate down to."""
    controller, sw, _, _ = _linked_pair()
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    table.cellWidget(1, 1).setEditText("2.5G")  # row 1 has no link
    assert table.item(1, 2).text() == "2.5"
    dialog.deleteLater()


def test_a_port_with_no_rate_at_either_end_shows_nothing(app):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    controller = AppController(repository=MagicMock())
    ap = controller.add_device("ap1", DeviceType.ACCESS_POINT, 0, 0)
    dialog = _dialog_for(controller, ap)
    table = dialog._interfaces.table
    wireless_rows = [
        row for row in range(table.rowCount())
        if table.cellWidget(row, 1).currentText() == "Wireless"
    ]
    assert table.item(wireless_rows[0], 2).text() == "—"
    dialog.deleteLater()


def test_the_negotiated_figure_follows_the_unit_that_reads_better(app):
    controller, sw, _, _ = _linked_pair(b_type=InterfaceType.ETH_10G)
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    table.cellWidget(0, 1).setEditText("500M")
    assert table.item(0, 2).text() == "500"
    assert table.cellWidget(0, 3).currentText() == "Mbps"

    table.cellWidget(0, 1).setEditText("2.5G")
    assert table.item(0, 2).text() == "2.5"
    assert table.cellWidget(0, 3).currentText() == "Gbps"
    dialog.deleteLater()


def test_the_rate_typed_as_a_type_reaches_the_link(app):
    """End to end through the GUI: both ports retyped, link negotiates."""
    controller, sw, rtr, link = _linked_pair()
    assert link.bandwidth_mbps == 1_000

    for device, typed in ((sw, "2.5G"), (rtr, "40G")):
        dialog = _dialog_for(controller, device)
        dialog._interfaces.table.cellWidget(0, 1).setEditText(typed)
        controller.edit_device_properties(
            device.id, device.device_model, device.loopback_ip, device.notes,
            device.native_vlan, device.status, dialog._interfaces.result_interfaces(),
        )
        dialog.deleteLater()

    assert controller.plan.get_link(link.id).bandwidth_mbps == 2_500
    controller.undo()
    controller.undo()
    assert controller.plan.get_link(link.id).bandwidth_mbps == 1_000


def test_peer_speeds_for_maps_each_port_to_its_far_end():
    controller, sw, _, _ = _linked_pair(b_type=InterfaceType.ETH_10G)
    peers = controller.plan.peer_speeds_for(sw)
    assert peers[sw.interfaces[0].id] == 10_000  # patched into the router
    assert peers[sw.interfaces[1].id] is None    # nothing on this one


def test_peer_lookup_ignores_links_to_ports_that_are_gone():
    """A link may name an interface id no device carries any more."""
    controller, sw, _, link = _linked_pair()
    link.b_interface_id = "vanished-port"
    peers = controller.plan.peer_speeds_for(sw)
    assert peers[sw.interfaces[0].id] is None


def test_normalizing_a_blank_type_restores_the_preset(app):
    from netplanner.gui.dialogs import _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_10G, "SFP28 DAC")
    combo.setEditText("")
    combo._normalize()
    assert combo.currentText() == "10 Gbps"
    assert combo.base_type() is InterfaceType.ETH_10G
    combo.deleteLater()


def test_normalizing_a_typed_preset_name_selects_that_preset(app):
    """Turning matching text into a real selection is what keeps
    base_type() and the dropdown showing the same thing."""
    from netplanner.gui.dialogs import _TYPE_CHOICES, _TypeCombo

    combo = _TypeCombo(InterfaceType.ETH_1G, None)
    combo.setEditText("25 gbps")
    combo._normalize()
    assert combo.currentIndex() == _TYPE_CHOICES.index(InterfaceType.ETH_25G)
    assert combo.base_type() is InterfaceType.ETH_25G
    assert combo.label_override() is None
    combo.deleteLater()
