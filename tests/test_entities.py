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
