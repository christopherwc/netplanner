"""Tests for VLAN colouring, usage queries, and highlight filtering."""

import pytest

from netplanner.domain.entities import (
    Device,
    DeviceType,
    Interface,
    Vlan,
    VlanMode,
)
from netplanner.domain.model import NetworkPlan


@pytest.fixture()
def plan() -> NetworkPlan:
    """A small plan: a switch with access + trunk ports, and a workstation."""
    plan = NetworkPlan("vlan test")
    plan.add_device(Device(
        name="sw1", device_type=DeviceType.SWITCH, native_vlan=99,
        interfaces=[
            Interface(name="Gig0/1", access_vlan=10),
            Interface(name="Gig0/2", access_vlan=20),
            Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20, 30]),
        ],
    ))
    plan.add_device(Device(
        name="ws1", device_type=DeviceType.WORKSTATION,
        interfaces=[Interface(name="eth0", access_vlan=30)],
    ))
    return plan


# ------------------------------------------------------------------ colours
def test_vlan_color_is_stable_for_an_id():
    from netplanner.export.vlans import vlan_color

    assert vlan_color(20) == vlan_color(20)


def test_vlan_colors_differ_across_nearby_ids():
    """Adjacent VLAN ids must not collide, or a legend is useless."""
    from netplanner.export.vlans import vlan_color

    colors = {vlan_color(v) for v in (10, 20, 30, 40)}
    assert len(colors) == 4


def test_vlan_color_wraps_beyond_palette():
    from netplanner.export.vlans import VLAN_PALETTE, vlan_color

    assert vlan_color(len(VLAN_PALETTE)) == vlan_color(0)


def test_vlan_color_does_not_depend_on_plan_contents(plan):
    """Colour comes from the id, not from ordering — deleting a device
    must never recolour the rest of the diagram."""
    from netplanner.export.vlans import plan_vlan_usage

    before = {u.vlan_id: u.color for u in plan_vlan_usage(plan)}
    plan.remove_device(next(d.id for d in plan.devices if d.name == "ws1"))
    after = {u.vlan_id: u.color for u in plan_vlan_usage(plan)}
    for vlan_id, color in after.items():
        assert before[vlan_id] == color


# ------------------------------------------------------------- membership
def test_interface_vlans_by_mode():
    from netplanner.export.vlans import interface_vlans

    assert interface_vlans(Interface(name="e0", access_vlan=10)) == {10}
    trunk = Interface(name="e1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20])
    assert interface_vlans(trunk) == {10, 20}


def test_device_vlans_includes_native(plan):
    from netplanner.export.vlans import device_vlans

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    assert device_vlans(sw1) == {99, 10, 20, 30}  # 99 is the native VLAN


# ------------------------------------------------------------------ usage
def test_plan_vlan_usage_counts_access_and_trunk(plan):
    from netplanner.export.vlans import plan_vlan_usage

    usage = {u.vlan_id: u for u in plan_vlan_usage(plan)}
    assert usage[10].access_interfaces == 1
    assert usage[10].trunk_interfaces == 1
    assert usage[30].access_interfaces == 1   # ws1's eth0
    assert usage[30].trunk_interfaces == 1    # sw1's trunk allows it


def test_plan_vlan_usage_is_sorted_by_id(plan):
    from netplanner.export.vlans import plan_vlan_usage

    ids = [u.vlan_id for u in plan_vlan_usage(plan)]
    assert ids == sorted(ids)


def test_plan_vlan_usage_records_native_devices(plan):
    from netplanner.export.vlans import plan_vlan_usage

    usage = {u.vlan_id: u for u in plan_vlan_usage(plan)}
    assert usage[99].native_on == ["sw1"]
    assert "sw1" in usage[99].device_names


def test_plan_vlan_usage_counts_devices_once(plan):
    """sw1 carries VLAN 10 on two ports but is still one device."""
    from netplanner.export.vlans import plan_vlan_usage

    usage = {u.vlan_id: u for u in plan_vlan_usage(plan)}
    assert usage[10].device_names.count("sw1") == 1
    assert usage[10].device_count == 1


def test_plan_vlan_usage_includes_named_catalog_vlans(plan):
    """A VLAN defined but never assigned still deserves a legend row."""
    from netplanner.export.vlans import plan_vlan_usage

    plan.add_vlan(Vlan(vlan_id=50, name="Guest"))
    usage = {u.vlan_id: u for u in plan_vlan_usage(plan)}
    assert 50 in usage
    assert usage[50].interface_count == 0
    assert "unused" in usage[50].summary


def test_vlan_usage_label_uses_catalog_name(plan):
    from netplanner.export.vlans import plan_vlan_usage

    plan.add_vlan(Vlan(vlan_id=20, name="Servers"))
    usage = {u.vlan_id: u for u in plan_vlan_usage(plan)}
    assert usage[20].label == "20 — Servers"
    assert usage[10].label == "VLAN 10"  # unnamed falls back to the id


def test_empty_plan_has_no_vlans():
    from netplanner.export.vlans import plan_vlan_usage

    assert plan_vlan_usage(NetworkPlan("empty")) == []


# ----------------------------------------------------------------- filter
def test_no_filter_matches_everything(plan):
    from netplanner.export.vlans import device_matches_filter

    for device in plan.devices:
        assert device_matches_filter(device, None)
        assert device_matches_filter(device, set())


def test_filter_selects_only_member_devices(plan):
    from netplanner.export.vlans import device_matches_filter

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    ws1 = next(d for d in plan.devices if d.name == "ws1")
    assert device_matches_filter(sw1, {20})
    assert not device_matches_filter(ws1, {20})   # ws1 is VLAN 30 only
    assert device_matches_filter(ws1, {30})


def test_filter_matches_via_native_vlan(plan):
    """A device with no interface on the VLAN still matches if it's native."""
    from netplanner.export.vlans import device_matches_filter

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    assert device_matches_filter(sw1, {99})


def test_filter_marks_individual_interfaces(plan):
    from netplanner.export.nodecard import build_card

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    card = build_card(sw1, vlan_filter={20})
    by_name = {b.top.split()[0]: b for b in card.iface_blocks}
    assert not by_name["Gig0/1"].matches_filter   # access VLAN 10
    assert by_name["Gig0/2"].matches_filter       # access VLAN 20
    assert by_name["Ten0/1"].matches_filter       # trunk allows 20


def test_filter_does_not_change_card_geometry(plan):
    """Toggling a filter must only recolour — never re-flow the diagram."""
    from netplanner.export.nodecard import build_card

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    plain = build_card(sw1)
    filtered = build_card(sw1, vlan_filter={20})
    excluded = build_card(sw1, vlan_filter={4000})
    assert plain.height == filtered.height == excluded.height
    assert plain.width == filtered.width == excluded.width


def test_card_carries_vlan_chip_colors(plan):
    from netplanner.export.nodecard import build_card
    from netplanner.export.vlans import vlan_color

    sw1 = next(d for d in plan.devices if d.name == "sw1")
    card = build_card(sw1)
    trunk_block = next(b for b in card.iface_blocks if b.top.startswith("Ten0/1"))
    assert trunk_block.vlan_colors == [vlan_color(v) for v in (10, 20, 30)]


def test_chip_count_is_capped_for_wide_trunks():
    """A trunk allowing many VLANs must not draw chips past the card edge."""
    from netplanner.export.nodecard import MAX_VLAN_CHIPS, build_card

    device = Device(
        name="sw9", device_type=DeviceType.SWITCH,
        interfaces=[Interface(
            name="Ten0/1", vlan_mode=VlanMode.TRUNK,
            trunk_vlans=list(range(10, 60)),
        )],
    )
    block = build_card(device).iface_blocks[0]
    assert len(block.vlan_colors) == MAX_VLAN_CHIPS


# ------------------------------------------------------- controller/export
def test_controller_exposes_vlan_usage():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    device = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    device.interfaces[0].access_vlan = 42
    assert 42 in {u.vlan_id for u in ctrl.vlan_usage()}


def test_controller_filter_flows_into_scene():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.export.renderer import build_scene

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    b = ctrl.add_device("ws1", DeviceType.WORKSTATION, 300, 0)
    a.interfaces[0].access_vlan = 20
    b.interfaces[0].access_vlan = 30

    scene = build_scene(ctrl.plan, vlan_filter={20})
    matches = {n.card.name: n.card.matches_filter for n in scene.nodes}
    assert matches["sw1"] is True
    assert matches["ws1"] is False


def test_filtered_export_still_renders(tmp_path):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    device = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    device.interfaces[0].access_vlan = 20
    ctrl.set_vlan_filter({20})
    ctrl.export_to_pdf(tmp_path / "f.pdf")
    ctrl.export_to_png(tmp_path / "f.png")
    assert (tmp_path / "f.pdf").stat().st_size > 0
    assert (tmp_path / "f.png").stat().st_size > 0
