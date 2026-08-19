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
