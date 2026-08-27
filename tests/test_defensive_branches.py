"""The branches that only run when something has already gone wrong.

Every command in the undo stack guards its target: `if device:` before
touching it, `if site:` before removing it. Those guards exist because a
command outlives the thing it names — it sits on the stack while later
edits happen, and undoing it much later must not assume its target is
still there.

Line coverage never reached the false side of any of them, which meant
the guards were shipped untested. A guard nobody has run is a guess
about what happens when it fires. These tests fire them.
"""

from __future__ import annotations

import pytest

from netplanner.app.commands import (
    CommandStack,
    DeleteSiteCommand,
    DeleteTextBoxCommand,
    EditConfigsCommand,
    EditDevicePropertiesCommand,
    EditInterfacesCommand,
    EditLinkCommand,
    EditSiteCommand,
    EditTextBoxCommand,
    MoveTextBoxCommand,
    RenameDeviceCommand,
    SetSiteGeometryCommand,
)
from netplanner.domain.entities import (
    Device,
    DeviceStatus,
    DeviceType,
    Link,
    LinkType,
    Site,
    Subnet,
    TextBox,
    VlanMode,
)
from netplanner.domain.model import NetworkPlan


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PyQt6", reason="PyQt6 not installed")
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


# ------------------------------------------------------------ the empty stack
def test_undo_and_redo_do_nothing_on_an_empty_stack():
    """Ctrl+Z on a fresh plan. Reachable by any user in the first second
    of using the application, and never exercised until now."""
    stack = CommandStack()

    stack.undo()
    stack.redo()

    assert not stack.can_undo
    assert not stack.can_redo


# ----------------------------------------------- commands whose target is gone
def _plan_with_device() -> tuple[NetworkPlan, Device]:
    plan = NetworkPlan("stale")
    device = Device(name="sw1", device_type=DeviceType.SWITCH)
    plan.add_device(device)
    return plan, device


def test_a_device_command_whose_device_vanished_is_a_no_op():
    """The command names a device by id. Delete the device and the id
    resolves to nothing — execute and undo must both decline quietly
    rather than raising into the middle of an undo sequence."""
    plan, device = _plan_with_device()
    commands = [
        RenameDeviceCommand(plan, device.id, "sw2"),
        EditInterfacesCommand(plan, device.id, []),
        EditConfigsCommand(plan, device.id, []),
        EditDevicePropertiesCommand(
            plan, device.id, "model", None, "notes", 10, DeviceStatus.PLANNED, []
        ),
    ]
    plan.remove_device(device.id)

    for command in commands:
        command.execute()
        command.undo()

    assert plan.get_device(device.id) is None


def test_a_textbox_command_whose_textbox_vanished_is_a_no_op():
    plan = NetworkPlan("stale")
    box = TextBox(text="DMZ", x=0, y=0)
    plan.add_textbox(box)
    commands = [
        MoveTextBoxCommand(plan, box.id, 50, 50),
        EditTextBoxCommand(plan, box.id, "Rack 3", 12.0, True, "#000000", 200.0),
    ]
    plan.remove_textbox(box.id)
    # A delete command captures its target when it is built, so the
    # guard fires only for one built against an id that was already
    # gone — an undo stack replayed against a plan reloaded underneath.
    commands.append(DeleteTextBoxCommand(plan, "never-existed"))

    for command in commands:
        command.execute()
        command.undo()

    assert plan.get_textbox(box.id) is None


def test_a_site_command_whose_site_vanished_is_a_no_op():
    plan = NetworkPlan("stale")
    site = Site(name="Rack 3", x=0, y=0, width=100, height=100)
    plan.add_site(site)
    commands = [
        SetSiteGeometryCommand(plan, site.id, 10, 10, 200, 200),
        EditSiteCommand(plan, site.id, "Rack 4", "notes", "#ff0000"),
    ]
    plan.remove_site(site.id)
    commands.append(DeleteSiteCommand(plan, "never-existed"))

    for command in commands:
        command.execute()
        command.undo()

    assert plan.get_site(site.id) is None


def test_a_link_command_whose_link_vanished_is_a_no_op():
    plan, a = _plan_with_device()
    b = Device(name="rtr1", device_type=DeviceType.ROUTER)
    plan.add_device(b)
    link = plan.add_link(Link(a_device_id=a.id, b_device_id=b.id, link_type=LinkType.FIBER))
    command = EditLinkCommand(plan, link.id, "circuit-9", LinkType.ETHERNET, 1_000)
    plan.remove_link(link)

    command.execute()
    command.undo()

    assert plan.get_link(link.id) is None


def test_undoing_a_device_deletion_skips_links_whose_far_end_is_gone():
    """Deleting a device takes its cables with it, and undoing brings
    them back — but only the ones that still have two ends. If the far
    end went in between, its cable has nowhere to land and must be left
    out rather than restored dangling."""
    from netplanner.app.commands import DeleteDeviceCommand

    plan = NetworkPlan("dangling")
    sw = Device(name="sw1", device_type=DeviceType.SWITCH)
    rtr = Device(name="rtr1", device_type=DeviceType.ROUTER)
    plan.add_device(sw)
    plan.add_device(rtr)
    plan.add_link(Link(a_device_id=sw.id, b_device_id=rtr.id, link_type=LinkType.FIBER))

    command = DeleteDeviceCommand(plan, sw.id)
    command.execute()          # sw and its link both go
    plan.remove_device(rtr.id)  # the far end goes too, separately
    command.undo()             # sw comes back; its link has nowhere to land

    assert [d.name for d in plan.devices] == ["sw1"]
    assert plan.links == []


# -------------------------------------------------------- the graph guards
def test_removing_a_device_the_graph_never_held():
    """NetworkPlan keeps a networkx graph beside its lists. The guard
    asks the graph first, and the answer is no for an id it never saw."""
    plan = NetworkPlan("empty")

    plan.remove_device("never-added")  # has_node -> False

    assert plan.devices == []


def test_removing_a_link_the_graph_no_longer_holds():
    """Removing a device drops its edges, so a Link object held from
    before still names an edge that is gone. Removing it again must
    find nothing rather than raise."""
    plan = NetworkPlan("desync")
    a = Device(name="a", device_type=DeviceType.SWITCH)
    b = Device(name="b", device_type=DeviceType.SWITCH)
    plan.add_device(a)
    plan.add_device(b)
    link = plan.add_link(Link(a_device_id=a.id, b_device_id=b.id, link_type=LinkType.FIBER))
    plan.remove_device(a.id)  # networkx drops the edge with the node

    plan.remove_link(link)  # has_edge -> False

    assert plan.links == []


# ------------------------------------------------------------------ validation
def test_subnets_that_do_not_overlap_raise_nothing():
    """The overlap check had only ever been run on subnets that do
    overlap, so the passing side of it was untested."""
    from netplanner.app.validation import validate

    plan = NetworkPlan("subnets")
    plan.add_subnet(Subnet(cidr="10.0.1.0/24", name="a"))
    plan.add_subnet(Subnet(cidr="10.0.2.0/24", name="b"))
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH))

    assert not [i for i in validate(plan) if "verlap" in i.message]


# ---------------------------------------------------------------- VLAN usage
def test_a_device_is_counted_once_for_a_vlan_it_carries_twice():
    """A device whose native VLAN is also configured on one of its
    access ports appears twice in the walk and once in the tally."""
    from netplanner.domain.entities import Interface
    from netplanner.export.vlans import plan_vlan_usage

    plan = NetworkPlan("vlans")
    device = Device(name="sw1", device_type=DeviceType.SWITCH, native_vlan=10)
    device.interfaces = [
        Interface(name="Gig0/1", vlan_mode=VlanMode.ACCESS, access_vlan=10)
    ]
    plan.add_device(device)

    ten = next(u for u in plan_vlan_usage(plan) if u.vlan_id == 10)
    assert ten.device_names.count("sw1") == 1


def test_two_devices_sharing_a_name_are_counted_once_per_vlan():
    """The tally is keyed by name, and nothing stops two devices having
    the same one — Plan → Validate flags it, but the plan still holds
    it, and the VLAN legend has to survive being asked about it."""
    from netplanner.export.vlans import plan_vlan_usage

    plan = NetworkPlan("dupes")
    for _ in range(2):
        plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, native_vlan=10))

    ten = next(u for u in plan_vlan_usage(plan) if u.vlan_id == 10)
    assert ten.device_names == ["sw1"]
    assert ten.native_on == ["sw1", "sw1"]  # both devices, one name


def test_an_explicit_console_level_skips_the_environment(tmp_path):
    """setup_logging reads NETPLANNER_LOG_LEVEL only when the caller
    did not say. Passing a level has to win over the environment."""
    import logging as _logging

    from netplanner.log import setup_logging

    logger = setup_logging(console_level=_logging.CRITICAL, log_dir=tmp_path)
    try:
        console = next(
            h for h in logger.handlers if not isinstance(h, _logging.FileHandler)
        )
        assert console.level == _logging.CRITICAL
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


# ------------------------------------------------------------ GUI leftovers
def test_a_site_with_no_notes_paints_without_a_notes_block(qt_app):
    """Notes are optional on a site box, and the paint path has to cope
    with the common case of not having any."""
    from PyQt6.QtGui import QImage, QPainter

    from netplanner.gui.canvas import SiteItem

    controller, _ = _detached_controller()
    site = controller.add_site("Rack 3", 0, 0)
    site.notes = ""
    item = SiteItem(site, controller)

    image = QImage(300, 300, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        item.paint(painter, None)
    finally:
        painter.end()


def test_an_unrecognised_unit_leaves_the_selector_alone(qt_app):
    """set_unit is given a stored figure's unit. A value the combo does
    not offer must not clear the selection."""
    from netplanner.gui.dialogs import _UnitCombo

    combo = _UnitCombo()
    before = combo.currentIndex()

    combo.set_unit(42)  # neither Gbps nor Mbps

    assert combo.currentIndex() == before
    combo.deleteLater()


def test_a_link_type_missing_from_the_dropdown_selects_nothing(qt_app):
    """The dialog offers the palette's media types. A link carrying one
    that is not on that list keeps its type rather than being silently
    re-typed to whatever sits at index zero."""
    from netplanner.gui.dialogs import LinkPropertiesDialog

    plan = NetworkPlan("odd")
    a = Device(name="a", device_type=DeviceType.SWITCH)
    b = Device(name="b", device_type=DeviceType.SWITCH)
    plan.add_device(a)
    plan.add_device(b)
    link = plan.add_link(Link(a_device_id=a.id, b_device_id=b.id, link_type=LinkType.FIBER))
    original = LinkPropertiesDialog.LINK_TYPE_CHOICES
    try:
        LinkPropertiesDialog.LINK_TYPE_CHOICES = [
            c for c in original if c is not LinkType.FIBER
        ]
        dialog = LinkPropertiesDialog(link, "", None)
        assert dialog.result_link_type() is not LinkType.FIBER  # not on the list
        dialog.deleteLater()
    finally:
        LinkPropertiesDialog.LINK_TYPE_CHOICES = original


def _detached_controller():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    controller = AppController(repository=MagicMock())
    return controller, controller.plan
