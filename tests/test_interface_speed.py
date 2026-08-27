"""Per-interface line rates.

A port states its own maximum outright, in the Maximum Interface Speed
column, read in whatever the Unit column says. Negotiated is derived
from the two ends and is never typed. A port has no other physical
specification: the rate is the whole of it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt

from netplanner.domain.entities import (
    DEFAULT_MAX_SPEED_MBPS,
    GBPS,
    MBPS,
    Device,
    DeviceType,
    Interface,
    Link,
    LinkType,
    VlanMode,
    format_speed_mbps,
    negotiated_speed_mbps,
)
from netplanner.domain.model import NetworkPlan
from netplanner.persistence.project_file import load_project, save_project
from netplanner.persistence.repository import PlanRepository, _interface_from_dict


# ------------------------------------------------------------------ formatting
def test_formatting_picks_the_readable_unit():
    assert format_speed_mbps(850) == "850 Mbps"
    assert format_speed_mbps(1_000) == "1 Gbps"
    assert format_speed_mbps(2_500) == "2.5 Gbps"


# ----------------------------------------------------------------------- model
def test_a_port_states_its_own_maximum():
    iface = Interface(name="eth0")
    assert iface.max_speed_mbps == DEFAULT_MAX_SPEED_MBPS
    assert iface.speed_mbps == 1_000
    assert iface.speed_label == "1 Gbps"

    iface.max_speed_mbps = 2_500
    assert iface.speed_mbps == 2_500
    assert iface.speed_label == "2.5 Gbps"


def test_a_blank_rate_means_unknown_not_zero():
    """A radio nobody has measured is carried as unmeasured, rather than
    filled in with whatever its media name suggests."""
    radio = Interface(name="wlan0", max_speed_mbps=None)
    assert radio.speed_mbps is None
    assert radio.speed_label == "rate unknown"

    radio.max_speed_mbps = 200
    assert radio.speed_mbps == 200
    assert radio.speed_label == "200 Mbps"


def test_a_link_runs_at_the_slower_end():
    fast = Interface(name="Gig0/0", max_speed_mbps=10_000)
    throttled = Interface(name="Gig0/1", max_speed_mbps=500)  # rate-limited handoff
    assert negotiated_speed_mbps(fast, throttled) == 500

    radio = Interface(name="wlan0", max_speed_mbps=None)
    port = Interface(name="eth0", max_speed_mbps=1_000)
    assert negotiated_speed_mbps(radio, port) == 1_000  # radio has no figure
    radio.max_speed_mbps = 200
    assert negotiated_speed_mbps(radio, port) == 200  # now it does


def _plan_with_link(a_mbps, b_mbps, link_type=LinkType.FIBER):
    plan = NetworkPlan("rates")
    a = Device(name="sw1", device_type=DeviceType.SWITCH)
    b = Device(name="rtr1", device_type=DeviceType.ROUTER)
    a.interfaces.append(Interface(name="Gig0/0", max_speed_mbps=a_mbps))
    b.interfaces.append(Interface(name="Gig0/0", max_speed_mbps=b_mbps))
    plan.add_device(a)
    plan.add_device(b)
    link = plan.add_link(
        Link(
            a_device_id=a.id, b_device_id=b.id, link_type=link_type,
            a_interface_id=a.interfaces[0].id, b_interface_id=b.interfaces[0].id,
        )
    )
    return plan, a, b, link


def test_auto_tracking_links_follow_a_rate_change():
    plan, a, _, link = _plan_with_link(10_000, 10_000)
    plan.recompute_auto_link_speeds()
    assert link.bandwidth_mbps == 10_000

    a.interfaces[0].max_speed_mbps = 2_500
    assert plan.recompute_auto_link_speeds() == [link.id]
    assert link.bandwidth_mbps == 2_500


def test_a_hand_entered_link_speed_still_wins():
    """bandwidth_auto off means the user's figure is not to be touched,
    whatever the ports say."""
    plan, a, _, link = _plan_with_link(1_000, 1_000, LinkType.ETHERNET)
    link.bandwidth_mbps = 300
    link.bandwidth_auto = False

    a.interfaces[0].max_speed_mbps = 2_500
    assert plan.recompute_auto_link_speeds() == []
    assert link.bandwidth_mbps == 300


# ----------------------------------------------------------------- persistence
def test_a_rate_survives_the_database(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "speeds.db")
    plan = NetworkPlan("speeds")
    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="Gig0/1", max_speed_mbps=2_500))
    plan.add_device(device)
    repo.save(plan)

    restored = repo.load(plan.id).devices[0].interfaces[0]
    assert restored.max_speed_mbps == 2_500
    assert restored.speed_mbps == 2_500


def test_an_unknown_rate_survives_as_unknown(tmp_path):
    """Round-tripping must not turn "not measured" into a number."""
    repo = PlanRepository(db_path=tmp_path / "unknown.db")
    plan = NetworkPlan("unknown")
    device = Device(name="ptp1", device_type=DeviceType.DISH_RADIO)
    device.interfaces.append(Interface(name="wlan0", max_speed_mbps=None))
    plan.add_device(device)
    repo.save(plan)

    assert repo.load(plan.id).devices[0].interfaces[0].max_speed_mbps is None


def test_a_rate_survives_a_project_file(tmp_path):
    plan = NetworkPlan("speeds")
    device = Device(name="ptp1", device_type=DeviceType.DISH_RADIO)
    device.interfaces.append(Interface(name="wlan0", max_speed_mbps=450))
    plan.add_device(device)
    path = tmp_path / "speeds.netplan"
    save_project(plan, path)

    assert load_project(path).devices[0].interfaces[0].max_speed_mbps == 450


def test_a_stored_media_name_is_dropped_rather_than_refused(tmp_path):
    """Ports used to carry a media label beside the rate. Loading a plan
    that still has one must not raise: the label is read past, and the
    rate it sat next to is what survives."""
    iface = _interface_from_dict(
        {
            "name": "Serial0/0",
            "interface_type": "1g",
            "type_label_override": "T1 serial",
            "speed_mbps_override": 2,
        }
    )
    assert iface.max_speed_mbps == 2
    assert not hasattr(iface, "type_label_override")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # A port that took its rate from its type keeps that rate.
        ({"name": "Gig0/0", "interface_type": "1g"}, 1_000),
        ({"name": "Ten0/1", "interface_type": "10g"}, 10_000),
        # Wireless had no nominal rate, and still has none.
        ({"name": "wlan0", "interface_type": "wireless"}, None),
        # A hand-set override was the rate, and stays the rate.
        ({"name": "wan0", "interface_type": "1g", "speed_mbps_override": 850}, 850),
        # Old enough to predate interface types entirely.
        ({"name": "eth0"}, 1_000),
    ],
)
def test_plans_written_before_the_rate_was_a_field_keep_their_speeds(payload, expected):
    """The rate used to be an override of the type, set only for ports
    the presets missed. Promoting the type's nominal rate to a stated
    one is what keeps an existing plan's links at the speeds they had.
    """
    assert _interface_from_dict(payload).max_speed_mbps == expected


# ------------------------------------------------------------------ the widget
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6", reason="PyQt6 not installed")
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


# --------------------------------------------------------- the interfaces table
def _dialog_for(controller, device):
    from netplanner.gui.dialogs import DevicePropertiesDialog

    return DevicePropertiesDialog(device, controller.plan.peer_speeds_for(device))


def _linked_pair(a_mbps=1_000, b_mbps=1_000):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    controller = AppController(repository=MagicMock())
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 400, 0)
    sw.interfaces[0].max_speed_mbps = a_mbps
    rtr.interfaces[0].max_speed_mbps = b_mbps
    link = controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id, b_interface_id=rtr.interfaces[0].id,
    )
    controller.plan.recompute_auto_link_speeds()
    return controller, sw, rtr, link


def test_the_columns_read_left_to_right_as_rate_unit_negotiated(app):
    """The order the table is meant to be read in: what the port can do,
    in what unit, and what that comes to once the far end has its say."""
    from netplanner.gui.dialogs import _COLUMN_LABELS

    assert _COLUMN_LABELS == [
        "Name", "Maximum Interface Speed", "Unit", "Negotiated",
        "IP address (CIDR)", "MAC address", "VLAN mode", "VLAN(s)",
    ]


def test_a_fresh_row_starts_at_one_gigabit(app):
    from netplanner.gui.dialogs import (
        COL_MAX_SPEED,
        COL_UNIT,
        DevicePropertiesDialog,
    )

    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces
    tab._append_row(
        "", DEFAULT_MAX_SPEED_MBPS, "", "00:00:00:00:00:00",
        VlanMode.ACCESS, "1", None,
    )
    row = tab.table.rowCount() - 1
    assert tab.table.cellWidget(row, COL_MAX_SPEED).text() == "1"
    assert tab.table.cellWidget(row, COL_UNIT).currentText() == "Gbps"
    dialog.deleteLater()


def test_a_rate_opens_in_the_unit_it_reads_best_in(app):
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_UNIT

    controller, sw, _, _ = _linked_pair(a_mbps=850)
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    assert table.cellWidget(0, COL_MAX_SPEED).text() == "850"
    assert table.cellWidget(0, COL_UNIT).currentText() == "Mbps"
    dialog.deleteLater()


def test_switching_the_unit_re_expresses_the_rate_rather_than_rescaling_it(app):
    """2.5 Gbps shown in Mbps is 2500, not 2.5. Rescaling instead would
    cut the port to a thousandth of its rate for one dropdown click."""
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_UNIT

    controller, sw, _, _ = _linked_pair(a_mbps=2_500, b_mbps=10_000)
    dialog = _dialog_for(controller, sw)
    tab = dialog._interfaces

    assert tab.table.cellWidget(0, COL_MAX_SPEED).text() == "2.5"
    tab.table.cellWidget(0, COL_UNIT).set_unit(MBPS)
    assert tab.table.cellWidget(0, COL_MAX_SPEED).text() == "2500"
    assert tab.result_interfaces()[0].max_speed_mbps == 2_500

    tab.table.cellWidget(0, COL_UNIT).set_unit(GBPS)
    assert tab.table.cellWidget(0, COL_MAX_SPEED).text() == "2.5"
    assert tab.result_interfaces()[0].max_speed_mbps == 2_500
    dialog.deleteLater()


def test_the_negotiated_column_is_capped_by_the_far_end(app):
    """The column's whole point: 40G into a 10G port is 10G."""
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_NEGOTIATED, COL_UNIT

    controller, sw, _, _ = _linked_pair(b_mbps=10_000)
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    assert table.item(0, COL_NEGOTIATED).text() == "1"  # 1G port, 10G peer
    assert table.cellWidget(0, COL_UNIT).currentText() == "Gbps"

    table.cellWidget(0, COL_MAX_SPEED).setText("40")
    assert table.item(0, COL_NEGOTIATED).text() == "10"  # the peer is the limit
    dialog.deleteLater()


def test_the_negotiated_column_is_not_editable(app):
    from netplanner.gui.dialogs import COL_NEGOTIATED

    controller, sw, _, _ = _linked_pair()
    dialog = _dialog_for(controller, sw)
    item = dialog._interfaces.table.item(0, COL_NEGOTIATED)
    assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
    dialog.deleteLater()


def test_an_unpatched_port_negotiates_to_its_own_rate(app):
    """With nothing on the far end there is nothing to negotiate down to."""
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_NEGOTIATED

    controller, sw, _, _ = _linked_pair()
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    table.cellWidget(1, COL_MAX_SPEED).setText("2.5")  # row 1 has no link
    assert table.item(1, COL_NEGOTIATED).text() == "2.5"
    dialog.deleteLater()


def test_a_port_with_no_rate_at_either_end_shows_nothing(app):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_NEGOTIATED

    controller = AppController(repository=MagicMock())
    ap = controller.add_device("ap1", DeviceType.ACCESS_POINT, 0, 0)
    dialog = _dialog_for(controller, ap)
    table = dialog._interfaces.table
    unrated_rows = [
        row for row in range(table.rowCount())
        if table.cellWidget(row, COL_MAX_SPEED).text() == ""
    ]
    assert table.item(unrated_rows[0], COL_NEGOTIATED).text() == "—"
    dialog.deleteLater()


def test_clearing_the_rate_makes_it_unknown_again(app):
    from netplanner.gui.dialogs import (
        COL_MAX_SPEED,
        COL_NEGOTIATED,
        DevicePropertiesDialog,
    )

    device = Device(name="ptp1", device_type=DeviceType.DISH_RADIO)
    device.interfaces.append(Interface(name="wlan0", max_speed_mbps=450))
    dialog = DevicePropertiesDialog(device)
    tab = dialog._interfaces

    tab.table.cellWidget(0, COL_MAX_SPEED).setText("")
    assert tab.table.item(0, COL_NEGOTIATED).text() == "—"
    assert tab.result_interfaces()[0].max_speed_mbps is None
    dialog.deleteLater()


def test_the_negotiated_figure_is_shown_in_the_rows_own_unit(app):
    """Choosing the unit here would move the selector under the person
    editing the maximum beside it."""
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_NEGOTIATED, COL_UNIT

    controller, sw, _, _ = _linked_pair(b_mbps=10_000)
    dialog = _dialog_for(controller, sw)
    table = dialog._interfaces.table

    assert table.cellWidget(0, COL_UNIT).currentText() == "Gbps"
    table.cellWidget(0, COL_MAX_SPEED).setText("0.5")
    assert table.item(0, COL_NEGOTIATED).text() == "0.5"
    assert table.cellWidget(0, COL_UNIT).currentText() == "Gbps"  # not switched
    dialog.deleteLater()


def test_a_rate_typed_into_the_table_reaches_the_link(app):
    """End to end through the GUI: both ports re-rated, link negotiates."""
    from netplanner.gui.dialogs import COL_MAX_SPEED

    controller, sw, rtr, link = _linked_pair()
    assert link.bandwidth_mbps == 1_000

    for device, typed in ((sw, "2.5"), (rtr, "40")):
        dialog = _dialog_for(controller, device)
        dialog._interfaces.table.cellWidget(0, COL_MAX_SPEED).setText(typed)
        controller.edit_device_properties(
            device.id, device.device_model, device.loopback_ip, device.notes,
            device.native_vlan, device.status, dialog._interfaces.result_interfaces(),
        )
        dialog.deleteLater()

    assert controller.plan.get_link(link.id).bandwidth_mbps == 2_500  # the slower end
    controller.undo()
    controller.undo()
    assert controller.plan.get_link(link.id).bandwidth_mbps == 1_000


def test_opening_and_accepting_the_dialog_does_not_move_the_rate(app):
    """Regression. A port's rate used to be re-derived from a media name
    on every open, so a port labelled "1000" had its stored figure
    multiplied by a thousand each time the dialog was accepted."""
    from netplanner.gui.dialogs import DevicePropertiesDialog

    device = Device(name="rtr1", device_type=DeviceType.ROUTER)
    device.interfaces.append(Interface(name="Gig0/0", max_speed_mbps=1_000))
    for _ in range(4):
        dialog = DevicePropertiesDialog(device)
        device.interfaces = dialog._interfaces.result_interfaces()
        dialog.deleteLater()
        assert device.interfaces[0].max_speed_mbps == 1_000


def test_editing_a_row_after_removing_the_one_above_it(app):
    """Regression guard. The refresh handlers used to close over the row
    index they were built with, so removing a row above left every
    handler below it pointed one row too far down."""
    from netplanner.gui.dialogs import COL_MAX_SPEED, COL_NEGOTIATED

    controller, sw, _, _ = _linked_pair()
    dialog = _dialog_for(controller, sw)
    tab = dialog._interfaces

    tab.table.selectRow(0)
    tab._remove_selected()
    tab.table.cellWidget(0, COL_MAX_SPEED).setText("2.5")
    assert tab.table.item(0, COL_NEGOTIATED).text() == "2.5"
    dialog.deleteLater()


def test_peer_speeds_for_maps_each_port_to_its_far_end():
    controller, sw, _, _ = _linked_pair(b_mbps=10_000)
    peers = controller.plan.peer_speeds_for(sw)
    assert peers[sw.interfaces[0].id] == 10_000  # patched into the router
    assert peers[sw.interfaces[1].id] is None    # nothing on this one


def test_peer_lookup_ignores_links_to_ports_that_are_gone():
    """A link may name an interface id no device carries any more."""
    controller, sw, _, link = _linked_pair()
    link.b_interface_id = "vanished-port"
    peers = controller.plan.peer_speeds_for(sw)
    assert peers[sw.interfaces[0].id] is None
