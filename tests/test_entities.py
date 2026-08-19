"""Smoke tests for the core model and validation."""

from netplanner.app.validation import Severity, validate
from netplanner.domain.entities import Device, DeviceType, Interface, Link
from netplanner.domain.model import NetworkPlan


def make_plan() -> NetworkPlan:
    plan = NetworkPlan("Test plan")
    r1 = plan.add_device(Device(name="core-rtr", device_type=DeviceType.ROUTER))
    sw1 = plan.add_device(Device(name="access-sw", device_type=DeviceType.SWITCH))
    plan.add_link(Link(a_device_id=r1.id, b_device_id=sw1.id))
    return plan


def test_devices_and_links():
    plan = make_plan()
    assert len(plan.devices) == 2
    assert len(plan.links) == 1
    assert plan.isolated_devices() == []


def test_duplicate_ip_detected():
    plan = make_plan()
    a, b = plan.devices
    a.interfaces.append(Interface(name="eth0", ip_address="10.0.0.1/24"))
    b.interfaces.append(Interface(name="eth0", ip_address="10.0.0.1/24"))
    issues = validate(plan)
    assert any(i.severity == Severity.ERROR and "Duplicate IP" in i.message for i in issues)


def test_isolated_device_warns():
    plan = make_plan()
    plan.add_device(Device(name="lonely-host"))
    issues = validate(plan)
    assert any("no links" in i.message for i in issues)


def test_auto_naming():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    assert ctrl.next_device_name(DeviceType.ROUTER) == "rtr1"
    ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    assert ctrl.next_device_name(DeviceType.ROUTER) == "rtr2"
    assert ctrl.next_device_name(DeviceType.SWITCH) == "sw1"


def test_styles_cover_all_types():
    from netplanner.export.styles import STYLES

    assert set(STYLES) == set(DeviceType)


def test_rename_undo_redo():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    ctrl.rename_device(d.id, "core-rtr")
    assert ctrl.plan.get_device(d.id).name == "core-rtr"
    ctrl.undo()
    assert ctrl.plan.get_device(d.id).name == "rtr1"
    ctrl.redo()
    assert ctrl.plan.get_device(d.id).name == "core-rtr"


def test_typed_links_and_new_devices():
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    dish_a = ctrl.add_device("dish1", DeviceType.DISH_RADIO, 0, 0)
    dish_b = ctrl.add_device("dish2", DeviceType.DISH_RADIO, 300, 0)
    ap = ctrl.add_device("apr1", DeviceType.AP_RADIO, 150, 100)
    link = ctrl.add_link(dish_a.id, dish_b.id, link_type=LinkType.WIRELESS, label="5 GHz PtP")
    ctrl.add_link(dish_b.id, ap.id, link_type=LinkType.ETHERNET)
    assert link.link_type == LinkType.WIRELESS
    assert link.label == "5 GHz PtP"
    assert len(ctrl.plan.links) == 2


def test_link_styles_cover_all_types():
    from netplanner.domain.entities import LinkType
    from netplanner.export.styles import LINK_STYLES

    assert set(LINK_STYLES) == set(LinkType)


def test_default_interfaces_created():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    rtr = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    dish = ctrl.add_device("dish1", DeviceType.DISH_RADIO, 0, 0)
    assert [i.name for i in rtr.interfaces] == ["Gig0/0", "Gig0/1", "Gig0/2", "Gig0/3"]
    assert any("PtP" in i.name for i in dish.interfaces)


def test_free_interfaces_shrink_as_links_use_them():
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("sw1", DeviceType.SWITCH, 100, 0)
    a_if = ctrl.free_interfaces(a.id)[0]
    b_if = ctrl.free_interfaces(b.id)[0]
    ctrl.add_link(a.id, b.id, LinkType.ETHERNET,
                  a_interface_id=a_if.id, b_interface_id=b_if.id)
    assert a_if.id not in {i.id for i in ctrl.free_interfaces(a.id)}
    assert len(ctrl.free_interfaces(a.id)) == 3  # router started with 4
    ctrl.undo()
    assert len(ctrl.free_interfaces(a.id)) == 4  # freed again after undo


def test_parallel_links_never_overlap():
    from netplanner.domain.entities import Link
    from netplanner.export.geometry import parallel_link_offsets

    links = [
        Link(a_device_id="A", b_device_id="B"),
        Link(a_device_id="B", b_device_id="A"),  # reversed order, same pair
        Link(a_device_id="A", b_device_id="B"),
        Link(a_device_id="A", b_device_id="C"),  # different pair
    ]
    offsets = parallel_link_offsets(links)
    ab_offsets = [offsets[l.id] for l in links[:3]]
    assert len(set(ab_offsets)) == 3  # all distinct -> no overlap
    assert offsets[links[3].id] == 0  # lone link stays centered


def test_edit_interfaces_undoable():
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import Interface
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("srv1", DeviceType.SERVER, 0, 0)
    ctrl.edit_interfaces(d.id, [Interface(name="bond0", ip_address="10.0.0.5/24")])
    assert [i.name for i in d.interfaces] == ["bond0"]
    ctrl.undo()
    assert [i.name for i in d.interfaces] == ["eth0", "eth1"]


def test_typed_default_interfaces():
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import InterfaceType
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    types = [i.interface_type for i in sw.interfaces]
    assert types.count(InterfaceType.ETH_1G) == 8
    assert types.count(InterfaceType.ETH_10G) == 2
    ap = ctrl.add_device("apr1", DeviceType.AP_RADIO, 0, 0)
    assert any(i.interface_type == InterfaceType.WIRELESS for i in ap.interfaces)


def test_interface_types_persist():
    from pathlib import Path
    from netplanner.domain.entities import Device, Interface, InterfaceType
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=Path("/tmp/iface_types.db"))
    plan = NetworkPlan("typed")
    plan.add_device(Device(
        name="core",
        device_type=DeviceType.SWITCH,
        interfaces=[
            Interface(name="Hun0/1", interface_type=InterfaceType.ETH_100G),
            Interface(name="Twe0/1", interface_type=InterfaceType.ETH_25G),
        ],
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    loaded_types = {i.name: i.interface_type for i in loaded.devices[0].interfaces}
    assert loaded_types["Hun0/1"] == InterfaceType.ETH_100G
    assert loaded_types["Twe0/1"] == InterfaceType.ETH_25G


def test_macs_default_to_all_zeros():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    macs = [i.mac_address for i in sw.interfaces]
    assert all(m == "00:00:00:00:00:00" for m in macs)


def test_macs_persist_and_legacy_payloads_get_placeholder():
    from pathlib import Path
    from netplanner.domain.entities import Device, Interface
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import (
        PlanRepository,
        _device_from_dict,
        _device_to_dict,
    )

    repo = PlanRepository(db_path=Path("/tmp/mac_test.db"))
    plan = NetworkPlan("macs")
    dev = Device(name="rtr1", device_type=DeviceType.ROUTER,
                 interfaces=[Interface(name="Gig0/0", mac_address="02:AA:BB:CC:DD:EE")])
    plan.add_device(dev)
    repo.save(plan)
    loaded = repo.load(plan.id)
    assert loaded.devices[0].interfaces[0].mac_address == "02:AA:BB:CC:DD:EE"

    # Legacy payload (pre-MAC): the all-zeros placeholder is generated on load
    legacy = _device_to_dict(dev)
    del legacy["interfaces"][0]["mac_address"]
    revived = _device_from_dict(legacy)
    assert revived.interfaces[0].mac_address == "00:00:00:00:00:00"


def test_device_properties_editable_and_undoable():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    assert d.native_vlan == 1  # default before any edit
    ctrl.edit_device_properties(
        d.id,
        device_model="Cisco ISR 4331",
        loopback_ip="10.255.0.1/32",
        notes="core router, uplinks to ISP-A and ISP-B",
        native_vlan=99,
        new_interfaces=d.interfaces,
    )
    assert d.device_model == "Cisco ISR 4331"
    assert d.loopback_ip == "10.255.0.1/32"
    assert "uplinks" in d.notes
    assert d.native_vlan == 99
    ctrl.undo()
    assert d.device_model == ""
    assert d.loopback_ip is None
    assert d.notes == ""
    assert d.native_vlan == 1
    ctrl.redo()
    assert d.device_model == "Cisco ISR 4331"


def test_device_properties_persist():
    from pathlib import Path
    from netplanner.domain.entities import Device
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=Path("/tmp/props_test.db"))
    plan = NetworkPlan("props")
    plan.add_device(Device(
        name="core",
        device_type=DeviceType.SWITCH,
        device_model="Mikrotik CRS326",
        loopback_ip="10.255.0.2/32",
        notes="rack 3, row B",
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    dev = loaded.devices[0]
    assert dev.device_model == "Mikrotik CRS326"
    assert dev.loopback_ip == "10.255.0.2/32"
    assert dev.notes == "rack 3, row B"


def test_nodecard_includes_new_sections():
    from netplanner.domain.entities import Device
    from netplanner.export.nodecard import build_card

    plain = build_card(Device(name="sw1", device_type=DeviceType.SWITCH))
    detailed = build_card(Device(
        name="sw2",
        device_type=DeviceType.SWITCH,
        device_model="Mikrotik CRS326",
        loopback_ip="10.255.0.3/32",
        notes="a fairly long note that should wrap across more than one line of text",
    ))
    assert detailed.device_model == "Mikrotik CRS326"
    assert "10.255.0.3/32" in detailed.loopback_line
    assert len(detailed.notes_lines) >= 2
    assert detailed.height > plain.height  # extra sections take extra space


def test_scene_never_clips_a_card_edge():
    """Regression test for a bug where the leftmost/topmost card's edge
    could land at a negative coordinate and get clipped off the page.

    This happened because normalization used device *centers* rather
    than the actual card bounding box, so any card wider/taller than
    the margin around its center would stick out past x=0/y=0.
    """
    from netplanner.domain.entities import Device
    from netplanner.export.renderer import build_scene
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("clip test")
    # Devices placed at (0, 0) and negative-ish relative offsets, with
    # enough interfaces/notes to make their cards wider than MARGIN.
    a = plan.add_device(Device(name="sw2", device_type=DeviceType.SWITCH, x=0, y=0))
    b = plan.add_device(Device(
        name="rtr1", device_type=DeviceType.ROUTER, x=0, y=300,
        loopback_ip="192.168.7.12/16",
    ))
    c = plan.add_device(Device(
        name="sw1", device_type=DeviceType.SWITCH, x=500, y=200,
        loopback_ip="192.168.8.1/16", notes="Hello world",
    ))

    scene = build_scene(plan)
    for node in scene.nodes:
        assert node.x >= 0, f"card left edge clipped: x={node.x}"
        assert node.y >= 0, f"card top edge clipped: y={node.y}"
        assert node.x + node.card.width <= scene.width
        assert node.y + node.card.height <= scene.height


def test_interface_vlan_defaults_to_access_vlan_1():
    from netplanner.domain.entities import Interface, VlanMode

    iface = Interface(name="eth0")
    assert iface.vlan_mode == VlanMode.ACCESS
    assert iface.access_vlan == 1
    assert iface.trunk_vlans == []
    assert iface.vlan_summary() == "VLAN 1"


def test_device_native_vlan_defaults_to_1():
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    assert d.native_vlan == 1


def test_interface_can_be_configured_as_trunk_with_multiple_vlans():
    from netplanner.domain.entities import Interface, VlanMode

    iface = Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20, 30])
    assert iface.vlan_mode == VlanMode.TRUNK
    assert iface.trunk_vlans == [10, 20, 30]
    assert iface.vlan_summary() == "Trunk: 10,20,30"


def test_empty_trunk_flagged_by_validation():
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import VlanMode
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    sw.interfaces[0].vlan_mode = VlanMode.TRUNK  # no trunk_vlans assigned
    issues = ctrl.validate_plan()
    assert any("trunk with no VLANs" in i.message for i in issues)


def test_vlan_fields_persist_through_sqlite():
    from pathlib import Path
    from netplanner.domain.entities import Device, Interface, VlanMode
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=Path("/tmp/vlan_test.db"))
    plan = NetworkPlan("vlans")
    plan.add_device(Device(
        name="sw1",
        device_type=DeviceType.SWITCH,
        native_vlan=99,
        interfaces=[
            Interface(name="Gig0/1", vlan_mode=VlanMode.ACCESS, access_vlan=10),
            Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20, 30]),
        ],
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    dev = loaded.devices[0]
    assert dev.native_vlan == 99
    by_name = {i.name: i for i in dev.interfaces}
    assert by_name["Gig0/1"].vlan_mode == VlanMode.ACCESS
    assert by_name["Gig0/1"].access_vlan == 10
    assert by_name["Ten0/1"].vlan_mode == VlanMode.TRUNK
    assert by_name["Ten0/1"].trunk_vlans == [10, 20, 30]


def test_vlan_fields_persist_through_netplan_json():
    from pathlib import Path
    from netplanner.domain.entities import Device, Interface, VlanMode
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("vlans json")
    plan.add_device(Device(
        name="sw1", device_type=DeviceType.SWITCH, native_vlan=42,
        interfaces=[Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[5, 15])],
    ))
    save_project(plan, Path("/tmp/vlan_test.netplan"))
    loaded = load_project(Path("/tmp/vlan_test.netplan"))
    dev = loaded.devices[0]
    assert dev.native_vlan == 42
    assert dev.interfaces[0].trunk_vlans == [5, 15]


def test_legacy_payload_without_vlan_fields_gets_defaults():
    from netplanner.domain.entities import Device, Interface
    from netplanner.persistence.repository import _device_from_dict, _device_to_dict

    dev = Device(name="rtr1", device_type=DeviceType.ROUTER,
                 interfaces=[Interface(name="Gig0/0")])
    legacy = _device_to_dict(dev)
    del legacy["native_vlan"]
    del legacy["interfaces"][0]["vlan_mode"]
    del legacy["interfaces"][0]["access_vlan"]
    del legacy["interfaces"][0]["trunk_vlans"]
    revived = _device_from_dict(legacy)
    assert revived.native_vlan == 1
    from netplanner.domain.entities import VlanMode
    assert revived.interfaces[0].vlan_mode == VlanMode.ACCESS
    assert revived.interfaces[0].access_vlan == 1


def test_nodecard_shows_native_vlan_and_interface_vlans():
    from netplanner.domain.entities import Device, Interface, VlanMode
    from netplanner.export.nodecard import build_card

    card = build_card(Device(
        name="sw1", device_type=DeviceType.SWITCH, native_vlan=7,
        interfaces=[
            Interface(name="Gig0/1", vlan_mode=VlanMode.ACCESS, access_vlan=10),
            Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[10, 20]),
        ],
    ))
    assert card.native_vlan_line == "Native VLAN: 7"
    assert card.iface_blocks[0].vlan == "VLAN 10"
    assert card.iface_blocks[1].vlan == "Trunk: 10,20"


def test_placeholder_mac_not_flagged_as_duplicate():
    """Regression test: the default all-zeros MAC is a placeholder, not
    a real address, so multiple un-configured interfaces sharing it
    should never trigger duplicate-MAC warnings.
    """
    from netplanner.app.controller import AppController
    from unittest.mock import MagicMock

    ctrl = AppController(repository=MagicMock())
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)   # 10 blank-MAC ports
    ctrl.add_device("rtr1", DeviceType.ROUTER, 300, 0)  # 4 more blank-MAC ports
    issues = ctrl.validate_plan()
    assert not any("Duplicate MAC" in i.message for i in issues)
