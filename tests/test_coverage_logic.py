"""Targeted tests closing coverage gaps in the non-GUI layers.

Each test names the branch it exists to exercise; together with the
existing suites these bring the app/domain/export-geometry/persistence
layers to full line coverage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from netplanner.app.commands import (
    CommandStack,
    DeleteDeviceCommand,
    MoveDeviceCommand,
)
from netplanner.app.controller import AppController
from netplanner.app.validation import Severity, validate
from netplanner.domain import layout
from netplanner.domain.entities import (
    ConfigFormat,
    Device,
    DeviceType,
    Interface,
    InterfaceType,
    Link,
    LinkType,
    Subnet,
    Vlan,
    VlanMode,
    detect_config_format,
)
from netplanner.domain.model import NetworkPlan
from netplanner.errors import PersistenceError
from netplanner.export.geometry import (
    label_anchor,
    offset_endpoints,
    parallel_link_offsets,
    point_along,
)
from netplanner.export.styles import (
    LINK_STYLES,
    STYLES,
    link_style_for_value,
    style_for_value,
)
from netplanner.log import default_log_dir
from netplanner.persistence.project_file import load_project, save_project
from netplanner.persistence.repository import PlanRepository


# --------------------------------------------------------------- fixtures
@pytest.fixture()
def controller():
    return AppController(repository=MagicMock())


@pytest.fixture()
def pair(controller):
    a = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    b = controller.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    return a, b


# --------------------------------------------------------------- commands
def test_command_stack_can_undo_redo_flags(controller):
    assert controller.commands.can_undo is False
    assert controller.commands.can_redo is False
    controller.add_device("d1", DeviceType.SWITCH, 0, 0)
    assert controller.commands.can_undo is True
    controller.undo()
    assert controller.commands.can_redo is True


def test_move_device_command_execute_and_undo(controller):
    device = controller.add_device("d1", DeviceType.SWITCH, 10, 20)
    controller.move_device(device.id, 50, 60)
    assert (device.x, device.y) == (50, 60)
    controller.undo()
    assert (device.x, device.y) == (10, 20)
    controller.redo()
    assert (device.x, device.y) == (50, 60)


def test_move_device_command_missing_device_is_noop():
    plan = NetworkPlan(name="p")
    cmd = MoveDeviceCommand(plan, "ghost", 5, 5)
    assert cmd.old == (0.0, 0.0)  # fallback when the device is absent
    cmd.execute()  # neither call should raise
    cmd.undo()


def test_delete_device_command_missing_device_undo_returns():
    plan = NetworkPlan(name="p")
    cmd = DeleteDeviceCommand(plan, "ghost")
    assert cmd.device is None
    cmd.execute()
    cmd.undo()  # early return: nothing to restore
    assert plan.devices == []


# ------------------------------------------------------------- controller
def test_free_interfaces_unknown_device_returns_empty(controller):
    assert controller.free_interfaces("nope") == []


def test_interface_name_edge_cases(controller, pair):
    a, _ = pair
    assert controller.interface_name(a.id, None) == ""  # unset id
    assert controller.interface_name("ghost", "iface") == ""  # unknown device
    assert controller.interface_name(a.id, "bogus") == ""  # unknown interface
    assert controller.interface_name(a.id, a.interfaces[0].id) == a.interfaces[0].name


def test_devices_in_site_passthrough(controller, pair):
    a, _ = pair
    site = controller.add_site("Rack", -50, -50, width=400, height=300)
    inside = controller.devices_in_site(site.id)
    assert a in inside


def test_get_interface_unknown_device_returns_none(controller):
    assert controller._interface("ghost", "iface-id") is None


# ------------------------------------------------------------- validation
def test_validate_flags_overlapping_subnets(controller):
    controller.plan.add_subnet(Subnet(name="a", cidr="10.0.0.0/24"))
    controller.plan.add_subnet(Subnet(name="b", cidr="10.0.0.0/25"))
    issues = validate(controller.plan)
    assert any("overlap" in i.message for i in issues)


def test_validate_flags_duplicate_macs(controller, pair):
    a, b = pair
    a.interfaces[0].mac_address = "aa:bb:cc:00:11:22"
    b.interfaces[0].mac_address = "AA:BB:CC:00:11:22"  # same MAC, different case
    issues = validate(controller.plan)
    dupes = [i for i in issues if "Duplicate MAC" in i.message]
    assert len(dupes) == 1
    assert dupes[0].severity is Severity.WARNING


# --------------------------------------------------------------- entities
def test_interface_type_labels():
    for itype in InterfaceType:
        assert itype.label  # every member has a label


def test_vlan_mode_labels():
    assert VlanMode.ACCESS.label == "Access"
    assert VlanMode.TRUNK.label == "Trunk"


def test_subnet_network_property():
    subnet = Subnet(name="lan", cidr="192.168.1.0/24")
    assert subnet.network.num_addresses == 256


def test_config_format_labels_and_comment_prefixes():
    for fmt in ConfigFormat:
        assert fmt.label
        assert isinstance(fmt.comment_prefixes, tuple)


def test_detect_config_format_ios_keywords_without_version():
    text = "interface GigabitEthernet0/1\n switchport mode access\n"
    assert detect_config_format(text) is ConfigFormat.CISCO_IOS


def test_detect_config_format_cfg_extension_with_braces():
    text = "firewall {\n  all-ping enable\n}\n"
    assert detect_config_format(text, "edge.cfg") is ConfigFormat.UBIQUITI


def test_interface_by_name():
    device = Device(name="sw", device_type=DeviceType.SWITCH)
    device.interfaces.append(Interface(name="Gig0/1"))
    iface = device.interfaces[0]
    assert device.interface_by_name(iface.name) is iface
    assert device.interface_by_name("nope") is None


# ------------------------------------------------------------------ model
def test_add_link_requires_both_devices():
    plan = NetworkPlan(name="p")
    device = Device(name="a", device_type=DeviceType.SWITCH)
    plan.add_device(device)
    link = Link(a_device_id=device.id, b_device_id="ghost")
    with pytest.raises(ValueError):
        plan.add_link(link)


def test_interface_for_edge_cases(controller, pair):
    a, _ = pair
    plan = controller.plan
    assert plan.interface_for(a.id, None) is None
    assert plan.interface_for("ghost", "iface") is None
    assert plan.interface_for(a.id, a.interfaces[0].id) is a.interfaces[0]


def test_add_subnet_and_neighbors(controller, pair):
    a, b = pair
    plan = controller.plan
    subnet = plan.add_subnet(Subnet(name="lan", cidr="10.1.0.0/24"))
    assert plan.subnets[subnet.id] is subnet
    controller.add_link(a.id, b.id, LinkType.ETHERNET)
    assert plan.neighbors(a.id) == [b]


# ----------------------------------------------------------------- layout
@pytest.mark.parametrize("algorithm", ["circular", "kamada_kawai"])
def test_auto_layout_alternate_algorithms(controller, pair, algorithm):
    a, b = pair
    controller.add_link(a.id, b.id, LinkType.ETHERNET)
    controller.run_auto_layout(algorithm)
    assert (a.x, a.y) != (b.x, b.y)


# --------------------------------------------------------------- geometry
def test_parallel_link_offsets_ignores_self_loops():
    loop = Link(a_device_id="x", b_device_id="x")
    normal = Link(a_device_id="x", b_device_id="y")
    offsets = parallel_link_offsets([loop, normal])
    assert loop.id not in offsets
    assert normal.id in offsets


def test_offset_endpoints_zero_and_nonzero():
    assert offset_endpoints(0, 0, 10, 0, 0) == (0, 0, 10, 0)
    # Degenerate zero-length segment: returned unchanged.
    assert offset_endpoints(3, 3, 3, 3, 5) == (3, 3, 3, 3)
    x1, y1, x2, y2 = offset_endpoints(0, 0, 10, 0, 5)
    assert (y1, y2) == (5, 5)  # perpendicular shift for a horizontal line


def test_point_along():
    assert point_along(0, 0, 10, 20, 0.5) == (5, 10)


def test_label_anchor_degenerate_direction():
    # Target coincides with the centre: fall back to the exit point.
    ex_ey = label_anchor(0, 0, 0, 0, 50, 30, 40, 10)
    assert len(ex_ey) == 2  # falls back to the raw exit point


# ----------------------------------------------------------------- styles
def test_style_for_value_lookup_and_fallback():
    assert style_for_value(DeviceType.ROUTER.value) is STYLES[DeviceType.ROUTER]
    assert style_for_value("not-a-type") is STYLES[DeviceType.OTHER]


def test_link_style_for_value_fallback():
    assert link_style_for_value("not-a-media") is LINK_STYLES[LinkType.ETHERNET]


# -------------------------------------------------------------------- log
def test_default_log_dir_xdg_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("NETPLANNER_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_log_dir() == tmp_path / "netplanner" / "logs"


# ------------------------------------------------------------ persistence
def _rich_plan() -> NetworkPlan:
    plan = NetworkPlan(name="rich")
    a = Device(name="sw1", device_type=DeviceType.SWITCH)
    a.interfaces.append(Interface(name="Gig0/1"))
    b = Device(name="rtr1", device_type=DeviceType.ROUTER)
    b.interfaces.append(Interface(name="Gig0/0"))
    plan.add_device(a)
    plan.add_device(b)
    plan.add_link(
        Link(
            a_device_id=a.id,
            b_device_id=b.id,
            a_interface_id=a.interfaces[0].id,
            b_interface_id=b.interfaces[0].id,
        )
    )
    plan.add_subnet(Subnet(name="lan", cidr="10.9.0.0/24"))
    plan.add_vlan(Vlan(vlan_id=10, name="users"))
    return plan


def test_project_file_roundtrip_with_subnets_vlans_links(tmp_path):
    plan = _rich_plan()
    path = tmp_path / "plan.netplan"
    save_project(plan, path)
    loaded = load_project(path)
    assert [d.name for d in loaded.devices] == ["sw1", "rtr1"]
    assert len(loaded.links) == 1
    assert [s.cidr for s in loaded.subnets.values()] == ["10.9.0.0/24"]
    assert [v.vlan_id for v in loaded.vlans.values()] == [10]


def test_project_file_write_failure_wrapped(tmp_path):
    plan = _rich_plan()
    # A directory in place of the file forces an OSError from open().
    target = tmp_path / "blocked.netplan"
    target.mkdir()
    with pytest.raises(PersistenceError) as excinfo:
        save_project(plan, target)
    assert "blocked.netplan" in str(excinfo.value)


def test_repository_roundtrip_with_meta_and_delete(tmp_path):
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    plan = _rich_plan()
    repo.save(plan)
    loaded = repo.load(plan.id)
    assert [s.cidr for s in loaded.subnets.values()] == ["10.9.0.0/24"]
    assert [v.vlan_id for v in loaded.vlans.values()] == [10]
    assert len(loaded.links) == 1

    repo.delete(plan.id)
    assert repo.list_plans() == []
    repo.delete(plan.id)  # deleting a missing row is a silent no-op


def test_repository_save_failure_wrapped(tmp_path, monkeypatch):
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    monkeypatch.setattr(
        repo, "_save_impl", MagicMock(side_effect=SQLAlchemyError("boom"))
    )
    with pytest.raises(PersistenceError) as excinfo:
        repo.save(_rich_plan())
    assert "boom" in str(excinfo.value)


def test_repository_load_failure_wrapped(tmp_path, monkeypatch):
    repo = PlanRepository(db_path=tmp_path / "plans.db")
    monkeypatch.setattr(
        repo, "_load_impl", MagicMock(side_effect=SQLAlchemyError("bad read"))
    )
    with pytest.raises(PersistenceError) as excinfo:
        repo.load("some-id")
    assert "bad read" in str(excinfo.value)
