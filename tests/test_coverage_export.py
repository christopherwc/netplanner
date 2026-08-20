"""Exporter and renderer coverage: optional card sections and failures.

Builds one deliberately maximal plan — models, loopbacks, configs,
notes, statuses, dashed media, port labels, a VLAN filter — so a single
PDF and PNG export walks every optional drawing branch, then checks the
error-wrapping paths that turn low-level failures into ExportError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from netplanner.domain.entities import (
    ConfigFile,
    Device,
    DeviceStatus,
    DeviceType,
    Interface,
    Link,
    LinkType,
    TextBox,
    VlanMode,
)
from netplanner.domain.model import NetworkPlan
from netplanner.errors import ExportError
from netplanner.export import pdf_exporter, png_exporter
from netplanner.export.png_exporter import _dashed_line, _draw_status_stripes
from netplanner.export.renderer import _port_name, build_scene


def maximal_plan() -> NetworkPlan:
    """A plan that exercises every optional rendering branch."""
    plan = NetworkPlan(name="Maximal")

    core = Device(
        name="core-sw",
        device_type=DeviceType.SWITCH,
        x=0,
        y=0,
        device_model="Cisco C9300",
        loopback_ip="10.255.0.1/32",
        notes="Primary distribution switch for the campus, racked in IDF 1. "
        "Replace the failing PSU during the next maintenance window.",
        status=DeviceStatus.PLANNED,  # single-color stripes
    )
    for i in range(8):  # enough ports to trigger the "+N more…" footer
        core.interfaces.append(
            Interface(
                name=f"Gig0/{i}",
                ip_address=f"10.0.{i}.1/24",
                access_vlan=10 if i % 2 else 20,
            )
        )
    core.interfaces[0].vlan_mode = VlanMode.TRUNK
    core.interfaces[0].trunk_vlans = [10, 20, 30]
    core.configs.append(
        ConfigFile(filename="core.cfg", content="hostname core\n")
    )

    edge = Device(
        name="edge-rtr",
        device_type=DeviceType.ROUTER,
        x=420,
        y=40,
        status=DeviceStatus.BROKEN,  # alternating hazard stripes
    )
    edge.interfaces.append(Interface(name="Gig0/0", ip_address="10.0.0.2/24"))

    ap = Device(name="ap-1", device_type=DeviceType.AP_RADIO, x=200, y=320)
    ap.interfaces.append(Interface(name="wlan0", access_vlan=30))

    for device in (core, edge, ap):
        plan.add_device(device)

    plan.add_link(
        Link(
            a_device_id=core.id,
            b_device_id=edge.id,
            a_interface_id=core.interfaces[0].id,
            b_interface_id=edge.interfaces[0].id,
            label="Core uplink",
        )
    )
    # Parallel second cable between the same pair: fan-out offsets.
    plan.add_link(
        Link(a_device_id=core.id, b_device_id=edge.id, link_type=LinkType.FIBER)
    )
    # Dashed media between core and the AP.
    plan.add_link(
        Link(a_device_id=core.id, b_device_id=ap.id, link_type=LinkType.WIRELESS)
    )
    plan.add_textbox(TextBox(text="DMZ boundary", x=100, y=-120))
    return plan


# ------------------------------------------------------------------ renderer
def test_build_scene_empty_plan_default_canvas():
    scene = build_scene(NetworkPlan(name="empty"))
    assert (scene.width, scene.height) == (400, 300)
    assert scene.nodes == [] and scene.edges == []


def test_build_scene_annotations_only():
    plan = NetworkPlan(name="notes only")
    plan.add_textbox(TextBox(text="hello world", x=50, y=60))
    scene = build_scene(plan)
    assert scene.nodes == []
    assert scene.texts


def test_port_name_missing_device_and_interface():
    plan = maximal_plan()
    device = plan.devices[0]
    assert _port_name(plan, "ghost", "iface") == ""
    assert _port_name(plan, device.id, "bogus") == ""
    assert _port_name(plan, device.id, None) == ""
    assert _port_name(plan, device.id, device.interfaces[0].id) == "Gig0/0"


def test_site_notes_wrapping_truncates(tmp_path):
    from netplanner.domain.entities import Site

    plan = NetworkPlan(name="sites")
    plan.add_device(Device(name="d", device_type=DeviceType.SWITCH, x=0, y=0))
    plan.add_site(
        Site(
            name="HQ",
            x=-100,
            y=-100,
            width=200,
            height=200,
            notes=("word " * 80) + "\n" + ("tail " * 40),  # forces line cap
        )
    )
    scene = build_scene(plan)
    (box,) = scene.sites
    assert box.notes_lines[-1].endswith("…")


# ----------------------------------------------------------------- exporters
def test_pdf_export_maximal_plan(tmp_path):
    plan = maximal_plan()
    path = tmp_path / "out.pdf"
    pdf_exporter.export_pdf(plan, path, vlan_filter={10})
    assert path.stat().st_size > 0


def test_png_export_maximal_plan(tmp_path):
    plan = maximal_plan()
    path = tmp_path / "out.png"
    png_exporter.export_png(plan, path, vlan_filter={10})
    with Image.open(path) as img:
        assert img.width > 0 and img.height > 0


def test_pdf_export_oserror_wrapped(tmp_path):
    plan = maximal_plan()
    missing_dir = tmp_path / "no" / "such" / "dir" / "out.pdf"
    with pytest.raises(ExportError) as excinfo:
        pdf_exporter.export_pdf(plan, missing_dir)
    assert "Maximal" in str(excinfo.value)


def test_png_export_oserror_wrapped(tmp_path):
    plan = maximal_plan()
    missing_dir = tmp_path / "no" / "such" / "dir" / "out.png"
    with pytest.raises(ExportError) as excinfo:
        png_exporter.export_png(plan, missing_dir)
    assert "Maximal" in str(excinfo.value)


def test_pdf_export_rendering_bug_wrapped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pdf_exporter,
        "_export_pdf_impl",
        lambda scene, path: (_ for _ in ()).throw(ValueError("render bug")),
    )
    with pytest.raises(ExportError) as excinfo:
        pdf_exporter.export_pdf(maximal_plan(), tmp_path / "x.pdf")
    assert "render bug" in str(excinfo.value)


def test_png_export_rendering_bug_wrapped(tmp_path, monkeypatch):
    monkeypatch.setattr(
        png_exporter,
        "_export_png_impl",
        lambda scene, path: (_ for _ in ()).throw(ValueError("render bug")),
    )
    with pytest.raises(ExportError) as excinfo:
        png_exporter.export_png(maximal_plan(), tmp_path / "x.png")
    assert "render bug" in str(excinfo.value)


# --------------------------------------------------------- PNG drawing helpers
def test_png_dashed_line_zero_length_is_noop():
    img = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(img)
    _dashed_line(draw, (5, 5), (5, 5), "#000000", 1, [4, 2])  # no crash


def test_png_status_stripes_degenerate_size_is_noop():
    img = Image.new("RGB", (10, 10), "white")
    _draw_status_stripes(img, 0, 0, 0.0, 5.0, ["#ff0000"])  # w <= 0
    _draw_status_stripes(img, 0, 0, 5.0, 5.0, [])  # no colors
