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
