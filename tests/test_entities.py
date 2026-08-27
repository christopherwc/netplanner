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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    assert ctrl.next_device_name(DeviceType.ROUTER) == "rtr1"
    ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    assert ctrl.next_device_name(DeviceType.ROUTER) == "rtr2"
    assert ctrl.next_device_name(DeviceType.SWITCH) == "sw1"


def test_styles_cover_all_types():
    from netplanner.export.styles import STYLES

    assert set(STYLES) == set(DeviceType)


def test_rename_undo_redo():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    ctrl.rename_device(d.id, "core-rtr")
    assert ctrl.plan.get_device(d.id).name == "core-rtr"
    ctrl.undo()
    assert ctrl.plan.get_device(d.id).name == "rtr1"
    ctrl.redo()
    assert ctrl.plan.get_device(d.id).name == "core-rtr"


def test_typed_links_and_new_devices():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    rtr = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    dish = ctrl.add_device("dish1", DeviceType.DISH_RADIO, 0, 0)
    assert [i.name for i in rtr.interfaces] == ["Gig0/0", "Gig0/1", "Gig0/2", "Gig0/3"]
    assert any("PtP" in i.name for i in dish.interfaces)


def test_free_interfaces_shrink_as_links_use_them():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

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
    ab_offsets = [offsets[link.id] for link in links[:3]]
    assert len(set(ab_offsets)) == 3  # all distinct -> no overlap
    assert offsets[links[3].id] == 0  # lone link stays centered


def test_edit_interfaces_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import Interface

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("srv1", DeviceType.SERVER, 0, 0)
    ctrl.edit_interfaces(d.id, [Interface(name="bond0", ip_address="10.0.0.5/24")])
    assert [i.name for i in d.interfaces] == ["bond0"]
    ctrl.undo()
    assert [i.name for i in d.interfaces] == ["eth0", "eth1"]


def test_typed_default_interfaces():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rates = [i.max_speed_mbps for i in sw.interfaces]
    assert rates.count(1_000) == 8
    assert rates.count(10_000) == 2
    # A radio's port has no rate until someone measures it.
    ap = ctrl.add_device("apr1", DeviceType.AP_RADIO, 0, 0)
    assert any(i.max_speed_mbps is None for i in ap.interfaces)


def test_interface_rates_persist(tmp_path):

    from netplanner.domain.entities import Device, Interface
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "iface_rates.db")
    plan = NetworkPlan("typed")
    plan.add_device(Device(
        name="core",
        device_type=DeviceType.SWITCH,
        interfaces=[
            Interface(name="Hun0/1", max_speed_mbps=100_000),
            Interface(name="Twe0/1", max_speed_mbps=25_000),
        ],
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    rates = {i.name: i.max_speed_mbps for i in loaded.devices[0].interfaces}
    assert rates["Hun0/1"] == 100_000
    assert rates["Twe0/1"] == 25_000


def test_macs_default_to_all_zeros():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    macs = [i.mac_address for i in sw.interfaces]
    assert all(m == "00:00:00:00:00:00" for m in macs)


def test_macs_persist_and_legacy_payloads_get_placeholder(tmp_path):

    from netplanner.domain.entities import Device, Interface
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import (
        PlanRepository,
        _device_from_dict,
        _device_to_dict,
    )

    repo = PlanRepository(db_path=tmp_path / "mac_test.db")
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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    assert d.native_vlan == 1  # default before any edit
    assert d.status == DeviceStatus.ACTIVE  # default before any edit
    ctrl.edit_device_properties(
        d.id,
        device_model="Cisco ISR 4331",
        loopback_ip="10.255.0.1/32",
        notes="core router, uplinks to ISP-A and ISP-B",
        native_vlan=99,
        status=DeviceStatus.PLANNED,
        new_interfaces=d.interfaces,
    )
    assert d.device_model == "Cisco ISR 4331"
    assert d.loopback_ip == "10.255.0.1/32"
    assert "uplinks" in d.notes
    assert d.native_vlan == 99
    assert d.status == DeviceStatus.PLANNED
    ctrl.undo()
    assert d.device_model == ""
    assert d.loopback_ip is None
    assert d.notes == ""
    assert d.status == DeviceStatus.ACTIVE
    assert d.native_vlan == 1
    ctrl.redo()
    assert d.device_model == "Cisco ISR 4331"


def test_device_properties_persist(tmp_path):

    from netplanner.domain.entities import Device
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "props_test.db")
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
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("clip test")
    # Devices placed at (0, 0) and negative-ish relative offsets, with
    # enough interfaces/notes to make their cards wider than MARGIN.
    plan.add_device(Device(name="sw2", device_type=DeviceType.SWITCH, x=0, y=0))
    plan.add_device(Device(
        name="rtr1", device_type=DeviceType.ROUTER, x=0, y=300,
        loopback_ip="192.168.7.12/16",
    ))
    plan.add_device(Device(
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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import VlanMode

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    sw.interfaces[0].vlan_mode = VlanMode.TRUNK  # no trunk_vlans assigned
    issues = ctrl.validate_plan()
    assert any("trunk with no VLANs" in i.message for i in issues)


def test_vlan_fields_persist_through_sqlite(tmp_path):

    from netplanner.domain.entities import Device, Interface, VlanMode
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "vlan_test.db")
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


def test_vlan_fields_persist_through_netplan_json(tmp_path):

    from netplanner.domain.entities import Device, Interface, VlanMode
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("vlans json")
    plan.add_device(Device(
        name="sw1", device_type=DeviceType.SWITCH, native_vlan=42,
        interfaces=[Interface(name="Ten0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[5, 15])],
    ))
    save_project(plan, tmp_path / "vlan_test.netplan")
    loaded = load_project(tmp_path / "vlan_test.netplan")
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
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)   # 10 blank-MAC ports
    ctrl.add_device("rtr1", DeviceType.ROUTER, 300, 0)  # 4 more blank-MAC ports
    issues = ctrl.validate_plan()
    assert not any("Duplicate MAC" in i.message for i in issues)


def test_device_status_defaults_to_active():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    assert d.status == DeviceStatus.ACTIVE


def test_broken_card_keeps_type_color_with_red_black_stripes():
    from netplanner.domain.entities import Device, DeviceStatus
    from netplanner.export.nodecard import STRIPE_BROKEN, build_card
    from netplanner.export.styles import style_for

    for dtype in DeviceType:
        active_card = build_card(Device(name="d", device_type=dtype))
        broken_card = build_card(Device(name="d", device_type=dtype, status=DeviceStatus.BROKEN))
        type_style = style_for(dtype)
        # Both keep the device-type color scheme...
        assert active_card.fill == type_style.fill
        assert broken_card.fill == type_style.fill
        assert broken_card.stroke == type_style.stroke
        # ...but only broken gets the alternating red/black stripe overlay.
        assert not active_card.striped
        assert broken_card.striped
        assert broken_card.stripe_colors == list(STRIPE_BROKEN)
        assert len(broken_card.stripe_colors) == 2  # two colors -> alternating


def test_planned_card_keeps_type_color_and_is_striped():
    from netplanner.domain.entities import Device, DeviceStatus, DeviceType
    from netplanner.export.nodecard import STRIPE_PLANNED, build_card
    from netplanner.export.styles import style_for

    style = style_for(DeviceType.ROUTER)
    card = build_card(
        Device(name="rtr1", device_type=DeviceType.ROUTER, status=DeviceStatus.PLANNED)
    )
    assert card.fill == style.fill  # type colors preserved
    assert card.stroke == style.stroke
    assert card.striped is True
    assert card.stripe_colors == [STRIPE_PLANNED]  # one color -> uniform gray hatch


def test_status_persists_through_sqlite(tmp_path):

    from netplanner.domain.entities import Device, DeviceStatus
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "status_test.db")
    plan = NetworkPlan("status")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, status=DeviceStatus.BROKEN))
    plan.add_device(Device(name="sw2", device_type=DeviceType.SWITCH, status=DeviceStatus.PLANNED))
    repo.save(plan)
    loaded = repo.load(plan.id)
    by_name = {d.name: d for d in loaded.devices}
    assert by_name["sw1"].status == DeviceStatus.BROKEN
    assert by_name["sw2"].status == DeviceStatus.PLANNED


def test_status_persists_through_netplan_json(tmp_path):

    from netplanner.domain.entities import Device, DeviceStatus
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("status json")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, status=DeviceStatus.PLANNED))
    save_project(plan, tmp_path / "status_test.netplan")
    loaded = load_project(tmp_path / "status_test.netplan")
    assert loaded.devices[0].status == DeviceStatus.PLANNED


def test_legacy_payload_without_status_defaults_to_active():
    from netplanner.domain.entities import Device, DeviceStatus
    from netplanner.persistence.repository import _device_from_dict, _device_to_dict

    dev = Device(name="rtr1", device_type=DeviceType.ROUTER)
    legacy = _device_to_dict(dev)
    del legacy["status"]
    revived = _device_from_dict(legacy)
    assert revived.status == DeviceStatus.ACTIVE


def test_active_card_has_no_stripes():
    from netplanner.domain.entities import Device
    from netplanner.export.nodecard import build_card

    card = build_card(Device(name="sw1", device_type=DeviceType.SWITCH))
    assert card.stripe_colors == []
    assert card.striped is False


def test_notes_wrapping_and_truncation():
    from netplanner.export.nodecard import (
        NOTES_CHARS_PER_LINE,
        NOTES_MAX_LINES,
        _wrap_notes,
    )

    assert _wrap_notes("") == []
    assert _wrap_notes("short note") == ["short note"]
    # Every wrapped line stays within the width limit
    wrapped = _wrap_notes("word " * 20)
    assert all(len(line) <= NOTES_CHARS_PER_LINE for line in wrapped)
    # Long notes are capped and end with an ellipsis
    long_wrapped = _wrap_notes("word " * 200)
    assert len(long_wrapped) == NOTES_MAX_LINES
    assert long_wrapped[-1].endswith("…")


def test_vlan_summary_formats():
    from netplanner.domain.entities import Interface, VlanMode

    assert Interface(name="e0", access_vlan=42).vlan_summary() == "VLAN 42"
    trunk = Interface(name="e1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[30, 10, 20])
    assert trunk.vlan_summary() == "Trunk: 10,20,30"  # sorted for display
    empty_trunk = Interface(name="e2", vlan_mode=VlanMode.TRUNK)
    assert empty_trunk.vlan_summary() == "Trunk: (none)"


def test_device_status_labels():
    from netplanner.domain.entities import DeviceStatus

    assert DeviceStatus.ACTIVE.label == "Active"
    assert DeviceStatus.PLANNED.label == "Planned"
    assert DeviceStatus.BROKEN.label == "Broken"
    # Every member has a label (guards against a new status missing one)
    assert all(s.label for s in DeviceStatus)


def test_status_change_is_single_undo_step():
    """Editing status together with other properties must undo as ONE step,
    not leave the device in a half-reverted state."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("fw1", DeviceType.FIREWALL, 0, 0)
    ctrl.edit_device_properties(
        d.id, device_model="ASA 5506", loopback_ip=None, notes="",
        native_vlan=5, status=DeviceStatus.BROKEN, new_interfaces=d.interfaces,
    )
    assert d.status == DeviceStatus.BROKEN and d.native_vlan == 5
    ctrl.undo()  # one undo reverts everything from that dialog OK
    assert d.status == DeviceStatus.ACTIVE
    assert d.native_vlan == 1
    assert d.device_model == ""


def test_status_survives_full_controller_save_load_cycle(tmp_path):

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "status_cycle.db")
    ctrl = AppController(repository=repo)
    d = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.edit_device_properties(
        d.id, device_model="", loopback_ip=None, notes="",
        native_vlan=1, status=DeviceStatus.PLANNED, new_interfaces=d.interfaces,
    )
    ctrl.save()
    plan_id = ctrl.plan.id
    ctrl.new_plan()  # wipe in-memory state
    ctrl.load(plan_id)
    assert ctrl.plan.devices[0].status == DeviceStatus.PLANNED


def test_all_statuses_export_without_error(tmp_path):
    """Every status must render through both exporters without raising —
    catches renderer/stripe regressions across the full status matrix."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus

    ctrl = AppController(repository=MagicMock())
    for i, status in enumerate(DeviceStatus):
        d = ctrl.add_device(f"dev{i}", DeviceType.SWITCH, i * 400, 0)
        ctrl.edit_device_properties(
            d.id, device_model="", loopback_ip=None, notes="",
            native_vlan=1, status=status, new_interfaces=d.interfaces,
        )
    ctrl.export_to_pdf(tmp_path / "statuses.pdf")
    ctrl.export_to_png(tmp_path / "statuses.png")
    assert (tmp_path / "statuses.pdf").stat().st_size > 0
    assert (tmp_path / "statuses.png").stat().st_size > 0


def test_broken_png_contains_red_and_black_stripe_pixels(tmp_path):
    """Pixel-level check: a broken device's exported PNG must actually
    contain both stripe colors, proving the alternation reaches the file.

    Stripes are drawn at STRIPE_ALPHA opacity over the card fill, so the
    expected pixel values are the alpha blend of each stripe color with
    the device type's fill color, not the raw stripe colors.
    """
    from unittest.mock import MagicMock

    from PIL import Image

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus
    from netplanner.export.nodecard import STRIPE_ALPHA, STRIPE_BROKEN
    from netplanner.export.styles import style_for

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("fw1", DeviceType.FIREWALL, 0, 0)
    ctrl.edit_device_properties(
        d.id, device_model="", loopback_ip=None, notes="",
        native_vlan=1, status=DeviceStatus.BROKEN, new_interfaces=d.interfaces,
    )
    png = tmp_path / "broken.png"
    ctrl.export_to_png(png)

    img = Image.open(png).convert("RGB")
    width, height = img.size
    # Sample every 3rd pixel; stripes are dense enough to be caught,
    # and this avoids Pillow's deprecated whole-image getdata()
    pixels = {img.getpixel((px, py)) for px in range(0, width, 3) for py in range(0, height, 3)}

    def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))

    fill = hex_to_rgb(style_for(DeviceType.FIREWALL).fill)

    def blended(stripe_hex: str) -> tuple[int, int, int]:
        s = hex_to_rgb(stripe_hex)
        return tuple(
            int(STRIPE_ALPHA * s[i] + (1 - STRIPE_ALPHA) * fill[i]) for i in range(3)
        )

    def near(target: tuple[int, int, int]) -> bool:
        # Antialiasing and downsampling shift colors slightly
        return any(sum(abs(p[i] - target[i]) for i in range(3)) < 90 for p in pixels)

    red, black = STRIPE_BROKEN
    assert near(blended(red)), "no red stripe pixels found in broken device PNG"
    assert near(blended(black)), "no black stripe pixels found in broken device PNG"


def test_auto_layout_assigns_distinct_scaled_positions():
    from netplanner.domain.entities import Device
    from netplanner.domain.layout import CANVAS_SCALE, auto_layout
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("layout")
    for i in range(5):
        plan.add_device(Device(name=f"d{i}", device_type=DeviceType.SWITCH))
    auto_layout(plan, "spring")
    positions = {(d.x, d.y) for d in plan.devices}
    assert len(positions) == 5  # nobody stacked on anybody
    assert all(abs(x) <= CANVAS_SCALE * 1.5 and abs(y) <= CANVAS_SCALE * 1.5
               for x, y in positions)


def test_auto_layout_unknown_algorithm_raises():
    import pytest

    from netplanner.domain.entities import Device
    from netplanner.domain.layout import auto_layout
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("layout")
    plan.add_device(Device(name="d0"))
    with pytest.raises(ValueError):
        auto_layout(plan, "does_not_exist")


def test_auto_layout_empty_plan_is_a_noop():
    from netplanner.domain.layout import auto_layout
    from netplanner.domain.model import NetworkPlan

    auto_layout(NetworkPlan("empty"), "spring")  # must not raise


def test_auto_layout_survives_missing_numpy(monkeypatch):
    """Regression test for the reported crash: networkx's layout
    algorithms raise ModuleNotFoundError when numpy isn't installed
    (it was missing from the project dependencies). auto_layout must
    degrade to the fallback circle layout instead of propagating."""
    import networkx as nx

    from netplanner.domain.entities import Device
    from netplanner.domain.layout import auto_layout
    from netplanner.domain.model import NetworkPlan

    def explode(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'numpy'")

    monkeypatch.setattr(nx, "spring_layout", explode)

    plan = NetworkPlan("no numpy")
    for i in range(3):
        plan.add_device(Device(name=f"d{i}"))
    auto_layout(plan, "spring")  # must not raise
    assert len({(d.x, d.y) for d in plan.devices}) == 3


def test_auto_layout_single_device_centers_in_fallback(monkeypatch):
    import networkx as nx

    from netplanner.domain.entities import Device
    from netplanner.domain.layout import auto_layout
    from netplanner.domain.model import NetworkPlan

    monkeypatch.setattr(
        nx, "spring_layout",
        lambda *a, **k: (_ for _ in ()).throw(ImportError("numpy")),
    )
    plan = NetworkPlan("single")
    d = plan.add_device(Device(name="only"))
    auto_layout(plan, "spring")
    assert (d.x, d.y) == (0.0, 0.0)


def _linked_pair():
    """Two devices joined by one cable, plus the controller driving them."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("sw1", DeviceType.SWITCH, 400, 0)
    link = ctrl.add_link(
        a.id, b.id, LinkType.ETHERNET,
        a_interface_id=ctrl.free_interfaces(a.id)[0].id,
        b_interface_id=ctrl.free_interfaces(b.id)[0].id,
    )
    return ctrl, a, b, link


def test_delete_link_keeps_devices():
    ctrl, _, _, link = _linked_pair()
    ctrl.delete_link(link)
    assert len(ctrl.plan.links) == 0
    assert len(ctrl.plan.devices) == 2  # devices survive


def test_delete_link_undo_restores_it():
    ctrl, _, _, link = _linked_pair()
    ctrl.delete_link(link)
    ctrl.undo()
    assert len(ctrl.plan.links) == 1
    ctrl.redo()
    assert len(ctrl.plan.links) == 0


def test_deleting_link_frees_its_interfaces():
    ctrl, a, _, link = _linked_pair()
    before = len(ctrl.free_interfaces(a.id))
    ctrl.delete_link(link)
    assert len(ctrl.free_interfaces(a.id)) == before + 1


def test_delete_device_cascades_to_its_links():
    ctrl, a, b, _ = _linked_pair()
    ctrl.delete_device(a.id)
    assert ctrl.plan.get_device(a.id) is None
    assert len(ctrl.plan.links) == 0  # incident cable went with it
    assert ctrl.plan.get_device(b.id) is not None  # far end untouched


def test_delete_device_undo_restores_device_and_its_links():
    """The subtle one: networkx drops incident edges with the node, so
    undo must put the cables back too, not just the device."""
    ctrl, a, _, link = _linked_pair()
    ctrl.delete_device(a.id)
    ctrl.undo()
    assert ctrl.plan.get_device(a.id) is not None
    assert len(ctrl.plan.links) == 1
    restored = ctrl.plan.links[0]
    # Interface assignments must survive the round trip
    assert restored.a_interface_id == link.a_interface_id
    assert restored.b_interface_id == link.b_interface_id


def test_delete_device_with_many_links_restores_all_of_them():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    hub = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    spokes = [ctrl.add_device(f"ws{i}", DeviceType.WORKSTATION, 200 * i, 300)
              for i in range(3)]
    for spoke in spokes:
        ctrl.add_link(
            hub.id, spoke.id, LinkType.ETHERNET,
            a_interface_id=ctrl.free_interfaces(hub.id)[0].id,
            b_interface_id=ctrl.free_interfaces(spoke.id)[0].id,
        )
    assert len(ctrl.plan.links) == 3
    ctrl.delete_device(hub.id)
    assert len(ctrl.plan.links) == 0
    ctrl.undo()
    assert len(ctrl.plan.links) == 3
    assert len(ctrl.plan.devices) == 4


def test_links_for_device_reports_both_directions():
    ctrl, a, b, _ = _linked_pair()
    assert len(ctrl.links_for_device(a.id)) == 1  # stored as the 'a' end
    assert len(ctrl.links_for_device(b.id)) == 1  # and found from the 'b' end


def test_delete_device_then_save_load_round_trip(tmp_path):
    """Deletions must actually persist, not reappear after a reload."""

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "delete_test.db")
    ctrl = AppController(repository=repo)
    a = ctrl.add_device("rtr1", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("sw1", DeviceType.SWITCH, 400, 0)
    ctrl.add_link(a.id, b.id, LinkType.ETHERNET)
    ctrl.save()
    ctrl.delete_device(a.id)
    ctrl.save()

    plan_id = ctrl.plan.id
    ctrl.new_plan()
    ctrl.load(plan_id)
    assert len(ctrl.plan.devices) == 1
    assert len(ctrl.plan.links) == 0


# --------------------------------------------------------------- config files
def _cisco_text() -> str:
    return (
        "! Last configuration change at 09:14\n"
        "version 15.2\n"
        "service timestamps debug datetime msec\n"
        "hostname core-sw1\n"
        "interface GigabitEthernet0/1\n"
        " switchport mode trunk\n"
    )


def test_config_format_detection_per_vendor():
    from netplanner.domain.entities import ConfigFormat, detect_config_format

    assert detect_config_format(_cisco_text()) is ConfigFormat.CISCO_IOS
    assert detect_config_format(
        "# jan/02/2026 by RouterOS 7.1\n/interface bridge add name=br0\n"
    ) is ConfigFormat.MIKROTIK
    assert detect_config_format("set system host-name ubnt-rtr\n") is ConfigFormat.UBIQUITI
    # Anything unrecognised stays plain text rather than guessing wrong.
    assert detect_config_format("just some notes about this box\n") is ConfigFormat.PLAIN_TEXT


def test_config_file_metadata():
    from netplanner.domain.entities import ConfigFile, ConfigFormat

    cfg = ConfigFile(filename="sw1.cfg", content=_cisco_text(),
                     config_format=ConfigFormat.CISCO_IOS)
    assert cfg.line_count == 6
    assert cfg.size_label.endswith("B")
    big = ConfigFile(filename="big.cfg", content="x" * 4096)
    assert big.size_label == "4.0 KB"


def test_devices_start_with_no_configs():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    assert ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0).configs == []


def test_edit_configs_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import ConfigFile

    ctrl = AppController(repository=MagicMock())
    d = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.edit_configs(d.id, [ConfigFile(filename="a.cfg", content="hostname a")])
    assert len(d.configs) == 1
    ctrl.undo()
    assert d.configs == []
    ctrl.redo()
    assert len(d.configs) == 1


def test_read_config_file_detects_and_records_source(tmp_path):
    from netplanner.app.controller import AppController
    from netplanner.domain.entities import ConfigFormat

    path = tmp_path / "core-sw1-running.cfg"
    path.write_text(_cisco_text())
    cfg = AppController.read_config_file(path)
    assert cfg.filename == "core-sw1-running.cfg"
    assert cfg.config_format is ConfigFormat.CISCO_IOS
    assert cfg.source_path == str(path)
    assert "hostname core-sw1" in cfg.content


def test_read_config_file_survives_non_utf8_bytes(tmp_path):
    """Vendor exports sometimes carry stray high bytes; import must not raise."""
    from netplanner.app.controller import AppController

    path = tmp_path / "odd.cfg"
    path.write_bytes(b"hostname sw1\ndescription caf\xe9 uplink\n")
    cfg = AppController.read_config_file(path)
    assert "hostname sw1" in cfg.content


def test_configs_persist_through_sqlite(tmp_path):
    from netplanner.domain.entities import ConfigFile, ConfigFormat, Device
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "cfg.db")
    plan = NetworkPlan("configs")
    plan.add_device(Device(
        name="sw1", device_type=DeviceType.SWITCH,
        configs=[
            ConfigFile(filename="run.cfg", content=_cisco_text(),
                       config_format=ConfigFormat.CISCO_IOS),
            ConfigFile(filename="notes.txt", content="rack 3"),
        ],
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    configs = loaded.devices[0].configs
    assert [c.filename for c in configs] == ["run.cfg", "notes.txt"]
    assert configs[0].config_format is ConfigFormat.CISCO_IOS
    assert "hostname core-sw1" in configs[0].content   # content travels with the plan


def test_configs_persist_through_netplan_json(tmp_path):
    from netplanner.domain.entities import ConfigFile, ConfigFormat, Device
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("configs json")
    plan.add_device(Device(name="rtr1", device_type=DeviceType.ROUTER,
                           configs=[ConfigFile(filename="r.rsc", content="/ip address add",
                                               config_format=ConfigFormat.MIKROTIK)]))
    path = tmp_path / "p.netplan"
    save_project(plan, path)
    loaded = load_project(path)
    assert loaded.devices[0].configs[0].config_format is ConfigFormat.MIKROTIK


def test_legacy_payload_without_configs_loads():
    """Plans saved before config attachments existed must still load."""
    from netplanner.domain.entities import Device
    from netplanner.persistence.repository import _device_from_dict, _device_to_dict

    legacy = _device_to_dict(Device(name="sw1", device_type=DeviceType.SWITCH))
    del legacy["configs"]
    assert _device_from_dict(legacy).configs == []


def test_card_shows_config_indicator():
    from netplanner.domain.entities import ConfigFile, Device
    from netplanner.export.nodecard import build_card

    plain = build_card(Device(name="sw1", device_type=DeviceType.SWITCH))
    assert plain.config_line == ""

    one = build_card(Device(name="sw2", device_type=DeviceType.SWITCH,
                            configs=[ConfigFile(filename="a.cfg")]))
    assert one.config_line == "1 config file attached"      # singular

    many = build_card(Device(name="sw3", device_type=DeviceType.SWITCH,
                             configs=[ConfigFile(filename=f"{i}.cfg") for i in range(3)]))
    assert many.config_line == "3 config files attached"    # plural
    assert many.height > plain.height  # the indicator takes vertical space


# ----------------------------------------------------------------- text boxes
def test_textbox_defaults():
    from netplanner.domain.entities import TextBox

    box = TextBox(text="DMZ")
    assert box.display_lines == ["DMZ"]
    assert box.width == 200.0
    assert box.bold is False
    assert box.height > 0


def test_textbox_wraps_at_width():
    from netplanner.domain.entities import TextBox

    box = TextBox(text="word " * 40, width=200, font_size=11)
    lines = box.display_lines
    assert len(lines) > 1
    # Every wrapped line must fit the estimated character budget.
    budget = int(200 / (11 * 0.55))
    assert all(len(line) <= budget for line in lines)


def test_textbox_preserves_explicit_blank_lines():
    from netplanner.domain.entities import TextBox

    box = TextBox(text="Title\n\nBody text here")
    assert box.display_lines[0] == "Title"
    assert box.display_lines[1] == ""  # the deliberate paragraph break survives


def test_textbox_bold_wraps_sooner_than_regular():
    """Bold glyphs are wider, so the same text must wrap earlier or it
    would overflow the box on the canvas."""
    from netplanner.domain.entities import TextBox

    text = "the quick brown fox jumps over the lazy dog"
    regular = TextBox(text=text, bold=False)
    bold = TextBox(text=text, bold=True)
    assert len(bold.display_lines) >= len(regular.display_lines)


def test_textbox_height_grows_with_line_count():
    from netplanner.domain.entities import TextBox

    short = TextBox(text="one line")
    long = TextBox(text="word " * 60)
    assert long.height > short.height


def test_empty_textbox_still_has_one_line():
    """Guards the renderers against an empty lines list."""
    from netplanner.domain.entities import TextBox

    assert TextBox(text="").display_lines == [""]


def test_plan_textbox_crud():
    from netplanner.domain.entities import TextBox
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("t")
    box = plan.add_textbox(TextBox(text="DMZ"))
    assert plan.get_textbox(box.id) is box
    plan.remove_textbox(box.id)
    assert plan.get_textbox(box.id) is None
    plan.remove_textbox("nonexistent")  # must not raise


def test_textboxes_are_not_topology():
    """Annotations must never leak into graph queries or validation."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.add_textbox("just a label", 10, 10)
    assert len(ctrl.plan.devices) == 1          # not counted as a device
    assert ctrl.plan.graph.number_of_nodes() == 1
    issues = ctrl.validate_plan()
    assert not any("label" in issue.message for issue in issues)


def test_add_textbox_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_textbox("note", 5, 5)
    assert len(ctrl.plan.textboxes) == 1
    ctrl.undo()
    assert len(ctrl.plan.textboxes) == 0
    ctrl.redo()
    assert len(ctrl.plan.textboxes) == 1


def test_move_textbox_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    box = ctrl.add_textbox("note", 0, 0)
    ctrl.move_textbox(box.id, 120, 250)
    assert (box.x, box.y) == (120, 250)
    ctrl.undo()
    assert (box.x, box.y) == (0, 0)


def test_edit_textbox_is_one_undo_step():
    """Content and all four formatting fields revert together."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    box = ctrl.add_textbox("before", 0, 0)
    ctrl.edit_textbox(box.id, "after", 20.0, True, "#c5221f", 320.0)
    assert (box.text, box.font_size, box.bold, box.color, box.width) == (
        "after", 20.0, True, "#c5221f", 320.0
    )
    ctrl.undo()
    assert box.text == "before"
    assert box.font_size == 11.0
    assert box.bold is False
    assert box.color == "#1a1a1a"


def test_delete_textbox_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    box = ctrl.add_textbox("note", 0, 0)
    ctrl.delete_textbox(box.id)
    assert len(ctrl.plan.textboxes) == 0
    ctrl.undo()
    assert ctrl.plan.get_textbox(box.id) is not None


def test_deleting_device_leaves_textboxes_alone():
    """A device cascade removes links, never annotations."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    device = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.add_textbox("rack label", 0, 0)
    ctrl.delete_device(device.id)
    assert len(ctrl.plan.textboxes) == 1


def test_textboxes_persist_through_sqlite(tmp_path):
    from netplanner.domain.entities import TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "tb.db")
    plan = NetworkPlan("annotated")
    plan.add_textbox(TextBox(text="DMZ", x=10, y=20, font_size=18,
                             bold=True, color="#c5221f", width=260))
    repo.save(plan)
    loaded = repo.load(plan.id)
    box = next(iter(loaded.textboxes.values()))
    assert (box.text, box.x, box.y, box.bold, box.color, box.width) == (
        "DMZ", 10, 20, True, "#c5221f", 260
    )


def test_textboxes_persist_through_netplan_json(tmp_path):
    from netplanner.domain.entities import TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("annotated json")
    plan.add_textbox(TextBox(text="Core rack", x=5, y=6))
    path = tmp_path / "p.netplan"
    save_project(plan, path)
    assert next(iter(load_project(path).textboxes.values())).text == "Core rack"


def test_legacy_plan_without_textboxes_loads(tmp_path):
    """Plans saved before annotations existed must still load."""
    import json

    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    path = tmp_path / "old.netplan"
    save_project(NetworkPlan("old"), path)
    doc = json.loads(path.read_text())
    del doc["textboxes"]
    path.write_text(json.dumps(doc))
    assert load_project(path).textboxes == {}


def test_scene_includes_textboxes_and_grows_to_fit():
    from netplanner.domain.entities import Device, TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=0, y=0))
    without = build_scene(plan)
    # An annotation far to the right must expand the page, not be clipped.
    plan.add_textbox(TextBox(text="far away note", x=900, y=0))
    with_box = build_scene(plan)
    assert len(with_box.texts) == 1
    assert with_box.width > without.width


def test_scene_normalizes_textbox_outside_device_bounds():
    """A note placed above/left of every device must land inside the page."""
    from netplanner.domain.entities import Device, TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=0, y=0))
    plan.add_textbox(TextBox(text="header", x=-800, y=-400))
    scene = build_scene(plan)
    assert scene.texts[0].x >= 0
    assert scene.texts[0].y >= 0


def test_text_only_plan_renders():
    """A plan with annotations but no devices is still a valid diagram."""
    from netplanner.domain.entities import TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("notes only")
    plan.add_textbox(TextBox(text="Design notes go here"))
    scene = build_scene(plan)
    assert len(scene.texts) == 1
    assert scene.nodes == []


def test_textboxes_export_to_pdf_and_png(tmp_path):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.add_textbox("DMZ ZONE", -300, -120, font_size=16, bold=True)
    ctrl.export_to_pdf(tmp_path / "a.pdf")
    ctrl.export_to_png(tmp_path / "a.png")
    assert (tmp_path / "a.pdf").stat().st_size > 0
    assert (tmp_path / "a.png").stat().st_size > 0


def test_annotation_panel_matches_export_page_background():
    """Regression: annotation text is dark, and the GUI canvas follows the
    system theme, so on a dark desktop the text was invisible. Text boxes
    now paint their own light panel; it must be the same color the
    exporters use for the page, or annotations would look different on
    screen than in the PDF/PNG."""
    from netplanner.export.png_exporter import BG_COLOR
    from netplanner.export.styles import DIAGRAM_BG

    assert BG_COLOR == DIAGRAM_BG
    # A light panel is what the dark annotation text assumes.
    assert DIAGRAM_BG.lower() in ("#ffffff", "#fff")


def test_textshape_carries_width_for_its_panel():
    """The renderers size the annotation panel from this, so it must
    survive the TextBox -> TextShape conversion."""
    from netplanner.domain.entities import TextBox
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    plan.add_textbox(TextBox(text="note", width=340))
    assert build_scene(plan).texts[0].width == 340


# ----------------------------------------------------------- port labelling
def test_card_exit_point_lands_on_the_card_boundary():
    from netplanner.export.geometry import card_exit_point

    # Card 190x300 centred at the origin, link heading right.
    x, y = card_exit_point(0, 0, 500, 0, 95, 150, gap=0)
    assert (round(x), round(y)) == (95, 0)
    # Heading down: the vertical edge is nearer.
    x, y = card_exit_point(0, 0, 0, 500, 95, 150, gap=0)
    assert (round(x), round(y)) == (0, 150)


def test_card_exit_point_applies_the_gap():
    from netplanner.export.geometry import card_exit_point

    x, _ = card_exit_point(0, 0, 500, 0, 95, 150, gap=6)
    assert round(x) == 101


def test_card_exit_point_handles_coincident_points():
    """Two devices at the same spot must not divide by zero."""
    from netplanner.export.geometry import card_exit_point

    assert card_exit_point(10, 10, 10, 10, 95, 150) == (10, 10)


def test_label_anchor_clears_the_card_entirely():
    """Regression: port labels were placed at a fixed 25%/75% along a
    centre-to-centre line, which lands inside the card whenever two
    devices sit close together — the label vanished under the card."""
    from netplanner.export.geometry import label_anchor

    half_w, half_h = 95, 150
    text_w, text_h = 30, 7
    # Short link: the old 25% rule would land at x=50, well inside the card.
    x, _ = label_anchor(0, 0, 200, 0, half_w, half_h, text_w, text_h)
    assert x - text_w / 2 >= half_w  # the label's near edge clears the card


def test_label_anchor_clears_the_card_on_short_vertical_links():
    from netplanner.export.geometry import label_anchor

    half_w, half_h = 95, 150
    _, y = label_anchor(0, 0, 0, 320, half_w, half_h, 30, 7)
    assert y - 7 / 2 >= half_h


def test_edge_shape_carries_card_half_extents():
    """Exporters need these to anchor labels; they must survive build_scene."""
    from netplanner.domain.entities import Device, Link
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    a = plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=0, y=0))
    b = plan.add_device(Device(name="rtr1", device_type=DeviceType.ROUTER, x=400, y=0))
    plan.add_link(Link(a_device_id=a.id, b_device_id=b.id))
    edge = build_scene(plan).edges[0]
    assert edge.a_half[0] > 0 and edge.a_half[1] > 0
    assert edge.b_half[0] > 0 and edge.b_half[1] > 0


def test_lift_above_line_moves_perpendicular_and_upward():
    from netplanner.export.geometry import lift_above_line

    # Horizontal cable: the label should rise, not shift along it.
    x, y = lift_above_line(100, 50, 0, 50, 200, 50, 8)
    assert (x, y) == (100, 42)


def test_lift_above_line_is_direction_independent():
    """A cable stored B->A must lift its labels the same way as A->B,
    or labels would flip side depending on click order."""
    from netplanner.export.geometry import lift_above_line

    forward = lift_above_line(100, 50, 0, 50, 200, 50, 8)
    reverse = lift_above_line(100, 50, 200, 50, 0, 50, 8)
    assert forward == reverse


def test_lift_above_line_handles_zero_length():
    from netplanner.export.geometry import lift_above_line

    assert lift_above_line(10, 10, 10, 10, 10, 10, 5) == (10, 5)


def test_label_anchor_lift_clears_the_cable():
    from netplanner.export.geometry import label_anchor

    on_line = label_anchor(0, 0, 400, 0, 95, 150, 30, 7, lift=0)
    lifted = label_anchor(0, 0, 400, 0, 95, 150, 30, 7, lift=8)
    assert lifted[1] < on_line[1]     # moved up off the cable
    assert lifted[0] == on_line[0]    # but not along it


def test_get_link_finds_and_misses():
    from netplanner.domain.entities import Device, Link
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("t")
    a = plan.add_device(Device(name="a", device_type=DeviceType.ROUTER))
    b = plan.add_device(Device(name="b", device_type=DeviceType.SWITCH))
    link = plan.add_link(Link(a_device_id=a.id, b_device_id=b.id))
    assert plan.get_link(link.id) is link
    assert plan.get_link("nope") is None


def test_edit_link_is_one_undo_step():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("a", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("b", DeviceType.SWITCH, 300, 0)
    link = ctrl.add_link(a.id, b.id, LinkType.ETHERNET)

    ctrl.edit_link(link.id, "Core uplink", LinkType.FIBER, 10000)
    assert (link.label, link.link_type, link.bandwidth_mbps) == (
        "Core uplink", LinkType.FIBER, 10000
    )
    ctrl.undo()
    assert (link.label, link.link_type, link.bandwidth_mbps) == (
        "", LinkType.ETHERNET, None
    )
    ctrl.redo()
    assert link.label == "Core uplink"


def test_link_label_persists_through_sqlite(tmp_path):
    from netplanner.domain.entities import Device, Link, LinkType
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "l.db")
    plan = NetworkPlan("labelled")
    a = plan.add_device(Device(name="a", device_type=DeviceType.ROUTER))
    b = plan.add_device(Device(name="b", device_type=DeviceType.SWITCH))
    plan.add_link(Link(
        a_device_id=a.id, b_device_id=b.id,
        label="MPLS circuit 4471", link_type=LinkType.WAN, bandwidth_mbps=500,
    ))
    repo.save(plan)
    loaded = repo.load(plan.id)
    link = loaded.links[0]
    assert link.label == "MPLS circuit 4471"
    assert link.link_type is LinkType.WAN
    assert link.bandwidth_mbps == 500


def test_labelled_link_exports(tmp_path):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("a", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("b", DeviceType.SWITCH, 400, 0)
    link = ctrl.add_link(a.id, b.id, LinkType.FIBER)
    ctrl.edit_link(link.id, "Core uplink", LinkType.FIBER, 10000)
    ctrl.export_to_pdf(tmp_path / "l.pdf")
    ctrl.export_to_png(tmp_path / "l.png")
    assert (tmp_path / "l.pdf").stat().st_size > 0
    assert (tmp_path / "l.png").stat().st_size > 0


# --------------------------------------------------------- link speed logic
def test_negotiated_speed_takes_the_slower_end():
    from netplanner.domain.entities import Interface, negotiated_speed_mbps

    def iface(mbps):
        return Interface(name="x", max_speed_mbps=mbps)

    assert negotiated_speed_mbps(
        iface(10_000), iface(1_000)
    ) == 1_000
    assert negotiated_speed_mbps(
        iface(100_000), iface(25_000)
    ) == 25_000
    # Order must not matter.
    assert negotiated_speed_mbps(
        iface(1_000), iface(100_000)
    ) == 1_000


def test_negotiated_speed_ignores_rateless_wireless():
    """A dish patched into a 1G port: the wired end is the best estimate
    available, so it's used rather than discarding the link's speed."""
    from netplanner.domain.entities import Interface, negotiated_speed_mbps

    def iface(mbps):
        return Interface(name="x", max_speed_mbps=mbps)

    assert negotiated_speed_mbps(
        iface(None), iface(1_000)
    ) == 1_000
    # Both ends rateless: nothing can be inferred.
    assert negotiated_speed_mbps(
        iface(None), iface(None)
    ) is None


def test_negotiated_speed_with_missing_interfaces():
    from netplanner.domain.entities import negotiated_speed_mbps

    assert negotiated_speed_mbps(None, None) is None


def test_new_link_auto_populates_the_slower_speed():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = ctrl.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    ten_gig = next(i for i in sw.interfaces if i.max_speed_mbps == 10_000)
    one_gig = rtr.interfaces[0]

    link = ctrl.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=ten_gig.id, b_interface_id=one_gig.id,
    )
    assert link.bandwidth_mbps == 1_000  # limited by the 1G end


def test_link_without_ports_has_no_auto_speed():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("a", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("b", DeviceType.SWITCH, 300, 0)
    assert ctrl.add_link(a.id, b.id, LinkType.ETHERNET).bandwidth_mbps is None


def test_link_derived_speed_reflects_current_ports():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = ctrl.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    ten_gig = next(i for i in sw.interfaces if i.max_speed_mbps == 10_000)
    link = ctrl.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=ten_gig.id, b_interface_id=rtr.interfaces[0].id,
    )
    # Upgrading the slower end raises what the link could carry. The
    # stored value is deliberately left alone; the dialog offers it.
    rtr.interfaces[0].max_speed_mbps = 25_000
    assert ctrl.link_derived_speed(link) == 10_000
    assert link.bandwidth_mbps == 1_000


def test_auto_speed_survives_undo_of_link_creation():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    a = ctrl.add_device("a", DeviceType.ROUTER, 0, 0)
    b = ctrl.add_device("b", DeviceType.SWITCH, 300, 0)
    link = ctrl.add_link(
        a.id, b.id, LinkType.ETHERNET,
        a_interface_id=a.interfaces[0].id, b_interface_id=b.interfaces[0].id,
    )
    assert link.bandwidth_mbps == 1_000
    ctrl.undo()
    assert ctrl.plan.links == []
    ctrl.redo()
    assert ctrl.plan.links[0].bandwidth_mbps == 1_000


# ------------------------------------------------- auto link speed tracking
def _linked_10g_to_1g():
    """A 10G switch port patched into a 1G router port."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import LinkType

    ctrl = AppController(repository=MagicMock())
    sw = ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    rtr = ctrl.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    ten_gig = next(i for i in sw.interfaces if i.max_speed_mbps == 10_000)
    link = ctrl.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=ten_gig.id, b_interface_id=rtr.interfaces[0].id,
    )
    return ctrl, sw, rtr, link


def _edit_port_rate(ctrl, device, index, new_mbps):
    """Re-rate a port the way the dialog does: new objects, same ids."""
    from dataclasses import replace

    from netplanner.domain.entities import DeviceStatus

    interfaces = [
        replace(iface, max_speed_mbps=new_mbps) if i == index else replace(iface)
        for i, iface in enumerate(device.interfaces)
    ]
    ctrl.edit_device_properties(
        device.id, device.device_model, device.loopback_ip, device.notes,
        device.native_vlan, DeviceStatus.ACTIVE, interfaces,
    )


def test_new_links_track_interface_speeds_by_default():
    _, _, _, link = _linked_10g_to_1g()
    assert link.bandwidth_auto is True
    assert link.bandwidth_mbps == 1_000


def test_link_speed_follows_a_port_upgrade():
    """The feature: raise the slow end and the link follows."""
    ctrl, _, rtr, link = _linked_10g_to_1g()
    _edit_port_rate(ctrl, rtr, 0, 25_000)
    assert link.bandwidth_mbps == 10_000  # now capped by the 10G end


def test_link_speed_follows_a_port_downgrade():
    ctrl, sw, _, link = _linked_10g_to_1g()
    index = next(
        i for i, iface in enumerate(sw.interfaces)
        if iface.max_speed_mbps == 10_000
    )
    _edit_port_rate(ctrl, sw, index, 1_000)
    assert link.bandwidth_mbps == 1_000


def test_recomputed_speed_undoes_with_the_interface_edit():
    """Undo restores the ports and re-derives from them, so the speed
    reverts as part of the same single undo step."""
    ctrl, _, rtr, link = _linked_10g_to_1g()
    _edit_port_rate(ctrl, rtr, 0, 25_000)
    assert link.bandwidth_mbps == 10_000
    ctrl.undo()
    assert link.bandwidth_mbps == 1_000
    ctrl.redo()
    assert link.bandwidth_mbps == 10_000


def test_manual_bandwidth_is_never_overwritten():
    """A measured or contracted rate must survive port changes."""
    from netplanner.domain.entities import LinkType

    ctrl, _, rtr, link = _linked_10g_to_1g()
    ctrl.edit_link(link.id, "", LinkType.FIBER, 500, bandwidth_auto=False)
    assert link.bandwidth_auto is False

    _edit_port_rate(ctrl, rtr, 0, 100_000)
    assert link.bandwidth_mbps == 500  # untouched


def test_reenabling_auto_resumes_tracking():
    from netplanner.domain.entities import LinkType

    ctrl, _, rtr, link = _linked_10g_to_1g()
    ctrl.edit_link(link.id, "", LinkType.FIBER, 500, bandwidth_auto=False)
    ctrl.edit_link(link.id, "", LinkType.FIBER, 1_000, bandwidth_auto=True)
    _edit_port_rate(ctrl, rtr, 0, 25_000)
    assert link.bandwidth_mbps == 10_000


def test_recompute_only_touches_links_that_changed():
    """The return value drives logging, so it must be accurate."""
    ctrl, _, rtr, link = _linked_10g_to_1g()
    assert ctrl.plan.recompute_auto_link_speeds() == []  # already correct
    rtr.interfaces[0].max_speed_mbps = 25_000
    assert ctrl.plan.recompute_auto_link_speeds() == [link.id]


def test_editing_an_unrelated_device_leaves_speeds_alone():
    ctrl, _, _, link = _linked_10g_to_1g()
    other = ctrl.add_device("ws1", DeviceType.WORKSTATION, 600, 0)
    _edit_port_rate(ctrl, other, 0, other.interfaces[0].max_speed_mbps)
    assert link.bandwidth_mbps == 1_000


def test_auto_flag_persists_through_sqlite(tmp_path):
    from netplanner.domain.entities import Device, Link, LinkType
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "auto.db")
    plan = NetworkPlan("auto")
    a = plan.add_device(Device(name="a", device_type=DeviceType.ROUTER))
    b = plan.add_device(Device(name="b", device_type=DeviceType.SWITCH))
    plan.add_link(Link(
        a_device_id=a.id, b_device_id=b.id, link_type=LinkType.WAN,
        bandwidth_mbps=500, bandwidth_auto=False,
    ))
    repo.save(plan)
    assert repo.load(plan.id).links[0].bandwidth_auto is False


def test_legacy_link_with_a_recorded_speed_does_not_start_tracking():
    """Plans saved before this feature: a stored figure was entered by
    hand, so it must not suddenly become overwritable."""
    from netplanner.domain.entities import Link
    from netplanner.persistence.repository import _link_from_dict, _link_to_dict

    payload = _link_to_dict(Link(a_device_id="a", b_device_id="b", bandwidth_mbps=500))
    del payload["bandwidth_auto"]
    assert _link_from_dict(payload).bandwidth_auto is False

    # ...while a legacy link with no figure is free to start tracking.
    payload = _link_to_dict(Link(a_device_id="a", b_device_id="b"))
    del payload["bandwidth_auto"]
    assert _link_from_dict(payload).bandwidth_auto is True


# ----------------------------------------------------------------- sites
def test_site_defaults_and_containment():
    from netplanner.domain.entities import Site

    site = Site(name="IDF 1", x=0, y=0, width=400, height=300)
    assert site.contains_point(100, 100)
    assert site.contains_point(0, 0)        # boundary counts as inside
    assert site.contains_point(400, 300)
    assert not site.contains_point(401, 100)
    assert not site.contains_point(100, -1)


def test_site_membership_is_positional():
    """Membership follows geometry, so dragging equipment in or out
    changes what a site contains with no extra bookkeeping."""
    from netplanner.domain.entities import Device, Site
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("t")
    site = plan.add_site(Site(name="IDF 1", x=0, y=0, width=400, height=300))
    inside = plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=100, y=100))
    plan.add_device(Device(name="sw2", device_type=DeviceType.SWITCH, x=900, y=100))

    assert [d.name for d in plan.devices_in_site(site.id)] == ["sw1"]
    inside.x = 900  # dragged out
    assert plan.devices_in_site(site.id) == []


def test_devices_in_missing_site_is_empty():
    from netplanner.domain.model import NetworkPlan

    assert NetworkPlan("t").devices_in_site("nope") == []


def test_site_crud_and_lookup():
    from netplanner.domain.entities import Site
    from netplanner.domain.model import NetworkPlan

    plan = NetworkPlan("t")
    site = plan.add_site(Site(name="IDF 1"))
    assert plan.get_site(site.id) is site
    plan.remove_site(site.id)
    assert plan.get_site(site.id) is None
    plan.remove_site("nonexistent")  # must not raise


def test_add_site_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_site("IDF 1", 0, 0)
    assert len(ctrl.plan.sites) == 1
    ctrl.undo()
    assert len(ctrl.plan.sites) == 0
    ctrl.redo()
    assert len(ctrl.plan.sites) == 1


def test_site_geometry_change_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    site = ctrl.add_site("IDF 1", 0, 0, width=400, height=300)
    ctrl.set_site_geometry(site.id, 50, 60, 700, 500)
    assert (site.x, site.y, site.width, site.height) == (50, 60, 700, 500)
    ctrl.undo()
    assert (site.x, site.y, site.width, site.height) == (0, 0, 400, 300)


def test_edit_site_is_one_undo_step():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    site = ctrl.add_site("IDF 1", 0, 0)
    ctrl.edit_site(site.id, "MDF", "Basement, UPS-backed", "#137333")
    assert (site.name, site.notes, site.color) == ("MDF", "Basement, UPS-backed", "#137333")
    ctrl.undo()
    assert site.name == "IDF 1"
    assert site.notes == ""


def test_deleting_a_site_leaves_its_devices_alone():
    """A site is a backdrop, not a container: removing it must not
    remove the equipment drawn on top of it."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    site = ctrl.add_site("IDF 1", 0, 0, width=400, height=300)
    ctrl.add_device("sw1", DeviceType.SWITCH, 100, 100)
    ctrl.delete_site(site.id)
    assert len(ctrl.plan.sites) == 0
    assert len(ctrl.plan.devices) == 1
    ctrl.undo()
    assert len(ctrl.plan.sites) == 1


def test_deleting_a_device_leaves_sites_alone():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_site("IDF 1", 0, 0, width=400, height=300)
    device = ctrl.add_device("sw1", DeviceType.SWITCH, 100, 100)
    ctrl.delete_device(device.id)
    assert len(ctrl.plan.sites) == 1


def test_sites_persist_through_sqlite(tmp_path):
    from netplanner.domain.entities import Site
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "sites.db")
    plan = NetworkPlan("sited")
    plan.add_site(Site(
        name="IDF 1", x=10, y=20, width=500, height=400,
        color="#137333", notes="Rack 3-5",
    ))
    repo.save(plan)
    site = next(iter(repo.load(plan.id).sites.values()))
    assert (site.name, site.x, site.y, site.width, site.height) == (
        "IDF 1", 10, 20, 500, 400
    )
    assert site.color == "#137333"
    assert site.notes == "Rack 3-5"


def test_sites_persist_through_netplan_json(tmp_path):
    from netplanner.domain.entities import Site
    from netplanner.domain.model import NetworkPlan
    from netplanner.persistence.project_file import load_project, save_project

    plan = NetworkPlan("sited json")
    plan.add_site(Site(name="MDF", x=5, y=6, width=300, height=200))
    path = tmp_path / "p.netplan"
    save_project(plan, path)
    assert next(iter(load_project(path).sites.values())).name == "MDF"


def test_legacy_site_without_geometry_gets_defaults():
    """Sites existed as name+notes before they had boxes; those plans
    must still load, taking the default geometry."""
    from netplanner.domain.entities import Site

    revived = Site(name="IDF 1", notes="old plan", id="abc")
    assert revived.width > 0 and revived.height > 0
    assert revived.x == 0 and revived.y == 0


def test_scene_includes_sites_and_grows_to_fit():
    from netplanner.domain.entities import Device, Site
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=0, y=0))
    without = build_scene(plan)
    plan.add_site(Site(name="Far", x=1200, y=0, width=400, height=300))
    with_site = build_scene(plan)
    assert len(with_site.sites) == 1
    assert with_site.width > without.width


def test_scene_normalizes_sites_outside_device_bounds():
    from netplanner.domain.entities import Device, Site
    from netplanner.domain.model import NetworkPlan
    from netplanner.export.renderer import build_scene

    plan = NetworkPlan("t")
    plan.add_device(Device(name="sw1", device_type=DeviceType.SWITCH, x=0, y=0))
    plan.add_site(Site(name="Wrapper", x=-900, y=-700, width=2000, height=1500))
    scene = build_scene(plan)
    assert scene.sites[0].x >= 0 and scene.sites[0].y >= 0


def test_sites_export_to_pdf_and_png(tmp_path):
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.add_site("IDF 1", -60, -60, width=500, height=400, notes="Rack 3-5")
    ctrl.add_device("sw1", DeviceType.SWITCH, 100, 100)
    ctrl.export_to_pdf(tmp_path / "s.pdf")
    ctrl.export_to_png(tmp_path / "s.png")
    assert (tmp_path / "s.pdf").stat().st_size > 0
    assert (tmp_path / "s.png").stat().st_size > 0


# ----------------------------------------------------------- plan renaming
def test_rename_plan_is_undoable():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    assert ctrl.plan.name == "Untitled plan"
    ctrl.rename_plan("HQ Campus")
    assert ctrl.plan.name == "HQ Campus"
    ctrl.undo()
    assert ctrl.plan.name == "Untitled plan"
    ctrl.redo()
    assert ctrl.plan.name == "HQ Campus"


def test_rename_plan_trims_and_rejects_blank():
    """A blank title would leave exports with an empty heading."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController

    ctrl = AppController(repository=MagicMock())
    ctrl.rename_plan("  Branch Office  ")
    assert ctrl.plan.name == "Branch Office"
    ctrl.rename_plan("   ")
    assert ctrl.plan.name == "Untitled plan"


def test_renamed_plan_is_the_export_title():
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.export.renderer import build_scene

    ctrl = AppController(repository=MagicMock())
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.rename_plan("HQ Campus")
    assert build_scene(ctrl.plan).title == "HQ Campus"


def test_plan_name_survives_save_and_load(tmp_path):
    from netplanner.app.controller import AppController
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "named.db")
    ctrl = AppController(repository=repo)
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)
    ctrl.rename_plan("HQ Campus")
    ctrl.save()

    plan_id = ctrl.plan.id
    ctrl.new_plan()
    assert ctrl.plan.name == "Untitled plan"  # a fresh plan resets the title
    ctrl.load(plan_id)
    assert ctrl.plan.name == "HQ Campus"


def test_renamed_plan_appears_in_plan_listings(tmp_path):
    """The name is how a saved plan is identified, so it must reach the
    repository listing rather than only the in-memory object."""
    from netplanner.app.controller import AppController
    from netplanner.persistence.repository import PlanRepository

    repo = PlanRepository(db_path=tmp_path / "named.db")
    ctrl = AppController(repository=repo)
    ctrl.rename_plan("Branch Office")
    ctrl.save()
    assert ("Branch Office") in [name for _, name in repo.list_plans()]
