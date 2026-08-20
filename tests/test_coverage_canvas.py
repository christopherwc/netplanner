"""Canvas interaction and painting coverage.

PyQt6 forbids constructing QGraphicsScene*Event objects directly, so
interactions are driven two ways:

- **QTest through the view** for paths that delegate to Qt's own
  machinery (drag commits, hover tracking, event fall-throughs): the
  view synthesizes real scene events exactly as at runtime.
- **FakeSceneEvent stubs** for handler branches that accept the event
  without calling ``super()``: those only read scenePos/pos/button and
  call accept(), so a stub exercises them faithfully.

Painting goes through scene.render() into an offscreen image so every
item's paint() runs for real. Popup menus and modal dialogs are patched
to make choices without blocking.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QMenu,
    QMessageBox,
)

from netplanner.app.controller import AppController  # noqa: E402
from netplanner.domain.entities import (  # noqa: E402
    ConfigFile,
    DeviceStatus,
    DeviceType,
    LinkType,
    VlanMode,
)
from netplanner.gui.canvas import (  # noqa: E402
    _CANCELLED,
    DeviceItem,
    LinkItem,
    NetworkCanvas,
    PlanScene,
    SiteItem,
    TextBoxItem,
)
from netplanner.gui.palette import SITE_TOOL, TEXT_TOOL  # noqa: E402

CANVAS_NS = "netplanner.gui.canvas"
NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


class FakeSceneEvent:
    """Stub for scene-event branches that never reach ``super()``."""

    def __init__(
        self,
        scene_pos: QPointF = QPointF(),
        item_pos: QPointF = QPointF(),
        button=LEFT,
    ):
        self._scene_pos = scene_pos
        self._item_pos = item_pos
        self._button = button
        self.accepted = False

    def scenePos(self) -> QPointF:  # noqa: N802 - Qt API shape
        return self._scene_pos

    def pos(self) -> QPointF:
        return self._item_pos

    def button(self):
        return self._button

    def screenPos(self) -> QPoint:  # noqa: N802
        return QPoint(10, 10)

    def accept(self) -> None:
        self.accepted = True


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def controller():
    return AppController(repository=MagicMock())


@pytest.fixture()
def canvas(app, controller):
    view = NetworkCanvas(controller)
    view.resize(1000, 800)
    view.show()  # offscreen: no real window, but geometry becomes valid
    yield view
    view.close()
    view.deleteLater()


@pytest.fixture()
def scene(canvas) -> PlanScene:
    return canvas._scene


@pytest.fixture()
def populated(controller, canvas, scene):
    """One of everything, dressed to hit every optional paint branch."""
    sw = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    sw.device_model = "C9300"
    sw.loopback_ip = "10.255.0.1/32"
    sw.notes = "distribution switch " * 6  # wraps into multiple lines
    sw.status = DeviceStatus.PLANNED
    sw.interfaces[0].access_vlan = 10
    sw.interfaces[1].vlan_mode = VlanMode.TRUNK
    sw.interfaces[1].trunk_vlans = [10, 20]
    sw.configs.append(ConfigFile(filename="run.cfg", content="hostname sw1\n"))

    rtr = controller.add_device("rtr1", DeviceType.ROUTER, 500, 0)
    rtr.status = DeviceStatus.BROKEN

    controller.add_link(
        sw.id, rtr.id, LinkType.FIBER,
        a_interface_id=sw.interfaces[0].id,
        b_interface_id=rtr.interfaces[0].id,
        label="Core uplink",
    )
    controller.add_link(sw.id, rtr.id, LinkType.WIRELESS)  # parallel + dashed
    controller.add_site("IDF 1", -700, -700, notes="rack 12 " * 20)
    controller.add_textbox("DMZ", 150, 320)
    scene.rebuild()
    return controller


def render_scene(scene: PlanScene) -> None:
    """Paint every item for real into an offscreen image."""
    image = QImage(1400, 1000, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    scene.render(painter)
    painter.end()


def vp(canvas: NetworkCanvas, x: float, y: float) -> QPoint:
    """Viewport point for scene coordinates, recentring first so the
    target always lies inside the widget."""
    canvas.centerOn(QPointF(x, y))
    return canvas.mapFromScene(QPointF(x, y))


def hover_move(canvas: NetworkCanvas, x: float, y: float) -> None:
    """Deliver a buttonless mouse move; the view turns it into item
    hover events. (QTest.mouseMove only warps the cursor, which the
    offscreen platform never dispatches.)"""
    point = vp(canvas, x, y)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(point),
        canvas.viewport().mapToGlobal(QPointF(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        NO_MOD,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def pick_action(menu: QMenu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


def ctx_event() -> FakeSceneEvent:
    return FakeSceneEvent()


# ---------------------------------------------------------------- painting
def test_render_detailed_cards(populated, scene):
    render_scene(scene)  # cards, stripes, links, labels, site, textbox


def test_render_compact_mode(populated, canvas, scene):
    canvas.set_show_details(False)
    render_scene(scene)
    canvas.set_show_details(True)


def test_render_with_selection_pending_and_filter(populated, canvas, scene):
    for item in scene.items():
        item.setSelected(True)
    device_item = next(iter(scene._device_items.values()))
    device_item.pending_source = True
    canvas.set_vlan_filter({10})  # dims non-members, mirrors to controller
    assert populated.vlan_filter == {10}
    render_scene(scene)
    canvas.set_vlan_filter(set())


def test_device_item_defaults_when_detached(populated):
    device = populated.plan.devices[0]
    item = DeviceItem(device, populated)  # never added to a scene
    assert item._vlan_filter() == set()
    assert item._details_on() is True


def test_textbox_hover_via_view(populated, canvas, scene):
    item = next(iter(scene._text_items.values()))
    center = item.mapToScene(item.boundingRect().center())
    hover_move(canvas, center.x(), center.y())
    assert item._hovered
    render_scene(scene)  # hovered border branch
    hover_move(canvas, center.x() + 600, center.y())
    assert not item._hovered


def test_site_grip_hover_via_view(populated, canvas, scene):
    item = next(iter(scene._site_items.values()))
    grip = item.mapToScene(
        QPointF(item.site.width - 4, item.site.height - 4)
    )
    hover_move(canvas, grip.x(), grip.y())
    assert item._hovered_grip
    render_scene(scene)  # grip strokes drawn while hovered
    body = item.mapToScene(QPointF(8, item.site.height / 2))
    hover_move(canvas, body.x(), body.y())
    assert not item._hovered_grip
    hover_move(canvas, grip.x() + 800, grip.y())  # off the site: hoverLeave


# ------------------------------------------------------------- device moves
def test_device_drag_commits_move(populated, canvas, scene):
    device = populated.plan.devices[0]
    item = scene._device_items[device.id]
    point = vp(canvas, device.x, device.y)
    QTest.mousePress(canvas.viewport(), LEFT, NO_MOD, point)
    item.setPos(50, 60)  # what Qt's drag machinery would have done
    QTest.mouseRelease(canvas.viewport(), LEFT, NO_MOD, point)
    assert (device.x, device.y) == (50, 60)
    populated.undo()
    scene.rebuild()


def test_device_release_without_move_is_noop(populated, canvas, scene):
    device = populated.plan.devices[0]
    depth = len(populated.commands._undo)
    point = vp(canvas, device.x, device.y)
    QTest.mouseClick(canvas.viewport(), LEFT, NO_MOD, point)
    assert len(populated.commands._undo) == depth


# ------------------------------------------------------------ context menus
def test_device_context_menu_rename(populated, scene):
    item = next(iter(scene._device_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Rename…")
    ), patch(
        f"{CANVAS_NS}.QInputDialog.getText", return_value=("renamed", True)
    ):
        item.contextMenuEvent(ctx_event())
    assert item.device.name == "renamed"


def test_device_rename_cancelled(populated, scene):
    item = next(iter(scene._device_items.values()))
    with patch(f"{CANVAS_NS}.QInputDialog.getText", return_value=("zzz", False)):
        item._rename()
    assert item.device.name != "zzz"


def test_device_context_menu_properties(populated, scene):
    item = next(iter(scene._device_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Edit properties…")
    ), patch(f"{CANVAS_NS}.DevicePropertiesDialog.exec", return_value=1):
        item.contextMenuEvent(ctx_event())
    # Unedited dialog: same values applied; configs unchanged means no
    # separate config command was pushed.
    assert populated.plan.get_device(item.device.id) is not None


def test_device_context_menu_properties_with_config_change(populated, scene):
    item = next(iter(scene._device_items.values()))
    device_id = item.device.id
    new_configs = [ConfigFile(filename="fresh.cfg", content="x\n")]
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Edit properties…")
    ), patch(f"{CANVAS_NS}.DevicePropertiesDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.DevicePropertiesDialog.result_configs",
        return_value=new_configs,
    ):
        item.contextMenuEvent(ctx_event())
    assert populated.plan.get_device(device_id).configs == new_configs


def test_device_context_menu_delete(populated, scene):
    item = next(iter(scene._device_items.values()))
    device_id = item.device.id
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Delete device")
    ), patch(
        f"{CANVAS_NS}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        item.contextMenuEvent(ctx_event())
    assert populated.plan.get_device(device_id) is None


def test_device_context_menu_dismissed(populated, scene):
    item = next(iter(scene._device_items.values()))
    with patch.object(QMenu, "exec", lambda self, *a: None):
        item.contextMenuEvent(ctx_event())  # no action chosen: nothing changes
    assert populated.plan.get_device(item.device.id) is item.device


def test_device_double_click_renames_in_select_mode(populated, canvas, scene):
    device = populated.plan.devices[0]
    with patch(f"{CANVAS_NS}.QInputDialog.getText", return_value=("dbl", True)):
        QTest.mouseDClick(
            canvas.viewport(), LEFT, NO_MOD, vp(canvas, device.x, device.y)
        )
    assert device.name == "dbl"


def test_device_double_click_with_tool_armed_passes_through(
    populated, canvas, scene
):
    device = populated.plan.devices[0]
    canvas.set_tool(DeviceType.SWITCH)
    with patch(f"{CANVAS_NS}.QInputDialog.getText") as prompt:
        QTest.mouseDClick(
            canvas.viewport(), LEFT, NO_MOD, vp(canvas, device.x, device.y)
        )
    prompt.assert_not_called()
    canvas.set_tool(None)


# -------------------------------------------------------------------- sites
def test_site_resize_via_grip(populated, canvas, scene):
    item = next(iter(scene._site_items.values()))
    w, h = item.site.width, item.site.height
    grip_scene = item.mapToScene(
        QPointF(item.site.width - 4, item.site.height - 4)
    )

    QTest.mousePress(
        canvas.viewport(), LEFT, NO_MOD, vp(canvas, grip_scene.x(), grip_scene.y())
    )
    assert item._resizing

    target = grip_scene + QPointF(40, 30)
    QTest.mouseMove(canvas.viewport(), vp(canvas, target.x(), target.y()))
    assert item.site.width == pytest.approx(w + 40, abs=2)

    QTest.mouseRelease(
        canvas.viewport(), LEFT, NO_MOD, vp(canvas, target.x(), target.y())
    )
    assert not item._resizing
    assert item.site.width == pytest.approx(w + 40, abs=2)
    populated.undo()
    assert (item.site.width, item.site.height) == (w, h)


def test_site_resize_clamps_to_minimum(populated, scene):
    item = next(iter(scene._site_items.values()))
    original = (item.site.width, item.site.height)
    item._resizing = True
    item._resize_origin = QPointF(0, 0)
    item._origin_size = original
    move = FakeSceneEvent(scene_pos=QPointF(-5000, -5000))
    item.mouseMoveEvent(move)
    assert move.accepted
    assert item.site.width == SiteItem.MIN_W
    assert item.site.height == SiteItem.MIN_H

    # Release through the resize branch commits via the command stack.
    release = FakeSceneEvent(scene_pos=QPointF(-5000, -5000))
    item.mouseReleaseEvent(release)
    assert release.accepted
    populated.undo()
    assert (item.site.width, item.site.height) == original


def test_site_body_press_and_drag_commits_move(populated, canvas, scene):
    item = next(iter(scene._site_items.values()))
    body = item.mapToScene(QPointF(8, item.site.height - 40))  # off the grip
    old_x, old_y = item.site.x, item.site.y
    point = vp(canvas, body.x(), body.y())
    QTest.mousePress(canvas.viewport(), LEFT, NO_MOD, point)
    assert not item._resizing
    item.setPos(old_x + 25, old_y + 15)
    QTest.mouseRelease(canvas.viewport(), LEFT, NO_MOD, point)
    assert (item.site.x, item.site.y) == (old_x + 25, old_y + 15)
    populated.undo()
    scene.rebuild()


def test_site_edit_via_double_click(populated, scene):
    item = next(iter(scene._site_items.values()))
    with patch(f"{CANVAS_NS}.SiteDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.SiteDialog.result_name", return_value="MDF"
    ), patch(
        f"{CANVAS_NS}.SiteDialog.result_notes", return_value=""
    ), patch(
        f"{CANVAS_NS}.SiteDialog.result_color", return_value="#137333"
    ):
        event = FakeSceneEvent()
        item.mouseDoubleClickEvent(event)
    assert event.accepted
    site = next(iter(populated.plan.sites.values()))
    assert (site.name, site.color) == ("MDF", "#137333")


def test_site_context_menu_edit_and_delete(populated, scene):
    item = next(iter(scene._site_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Edit site…")
    ), patch(f"{CANVAS_NS}.SiteDialog.exec", return_value=0):
        item.contextMenuEvent(ctx_event())  # cancelled edit: no change

    item = next(iter(scene._site_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Delete site")
    ), patch(
        f"{CANVAS_NS}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        item.contextMenuEvent(ctx_event())
    assert populated.plan.sites == {}


# -------------------------------------------------------------------- links
def test_link_shape_is_widened(populated, scene):
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    assert not link_item.shape().isEmpty()


def test_link_edit_via_double_click(populated, scene):
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    with patch(f"{CANVAS_NS}.LinkPropertiesDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.LinkPropertiesDialog.result_label", return_value="edited"
    ), patch(
        f"{CANVAS_NS}.LinkPropertiesDialog.result_link_type",
        return_value=LinkType.WAN,
    ), patch(
        f"{CANVAS_NS}.LinkPropertiesDialog.result_bandwidth", return_value=100
    ), patch(
        f"{CANVAS_NS}.LinkPropertiesDialog.result_bandwidth_auto",
        return_value=False,
    ):
        link_item.mouseDoubleClickEvent(FakeSceneEvent())
    assert link_item.link.label == "edited"
    assert link_item.link.link_type is LinkType.WAN


def test_link_endpoint_summary_with_missing_device(populated, scene):
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    assert "↔" in link_item._endpoint_summary()
    original = link_item.link.a_device_id
    link_item.link.a_device_id = "ghost"
    assert link_item._endpoint_summary().startswith("?")
    link_item.link.a_device_id = original


def test_link_context_menu_delete(populated, scene):
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    count = len(populated.plan.links)
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Delete link")
    ):
        link_item.contextMenuEvent(ctx_event())
    assert len(populated.plan.links) == count - 1


def test_link_context_menu_edit_cancelled(populated, scene):
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Edit link…")
    ), patch(f"{CANVAS_NS}.LinkPropertiesDialog.exec", return_value=0):
        link_item.contextMenuEvent(ctx_event())  # dialog cancelled: no edit


# ---------------------------------------------------------------- textboxes
def test_textbox_drag_commit(populated, canvas, scene):
    item = next(iter(scene._text_items.values()))
    old = (item.textbox.x, item.textbox.y)
    center = item.mapToScene(item.boundingRect().center())
    point = vp(canvas, center.x(), center.y())
    QTest.mousePress(canvas.viewport(), LEFT, NO_MOD, point)
    item.setPos(old[0] + 10, old[1] + 10)
    QTest.mouseRelease(canvas.viewport(), LEFT, NO_MOD, point)
    assert (item.textbox.x, item.textbox.y) == (old[0] + 10, old[1] + 10)
    populated.undo()
    scene.rebuild()


def test_textbox_edit_via_double_click(populated, scene):
    item = next(iter(scene._text_items.values()))
    with patch(f"{CANVAS_NS}.TextBoxDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_text", return_value="edited note"
    ), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_font_size", return_value=14.0
    ), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_bold", return_value=True
    ), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_color", return_value="#1a56db"
    ), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_width", return_value=240.0
    ):
        item.mouseDoubleClickEvent(FakeSceneEvent())
    textbox = next(iter(populated.plan.textboxes.values()))
    assert textbox.text == "edited note"
    assert textbox.bold is True


def test_textbox_context_menu_edit_and_delete(populated, scene):
    item = next(iter(scene._text_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Edit text…")
    ), patch(f"{CANVAS_NS}.TextBoxDialog.exec", return_value=0):
        item.contextMenuEvent(ctx_event())  # cancelled

    item = next(iter(scene._text_items.values()))
    with patch.object(
        QMenu, "exec", lambda self, *a: pick_action(self, "Delete text box")
    ), patch(
        f"{CANVAS_NS}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        item.contextMenuEvent(ctx_event())
    assert populated.plan.textboxes == {}


# --------------------------------------------------------- tool-driven clicks
def fake_press(scene: PlanScene, x: float, y: float) -> FakeSceneEvent:
    event = FakeSceneEvent(scene_pos=QPointF(x, y))
    scene.mousePressEvent(event)
    return event


def test_right_click_falls_through(populated, canvas, scene):
    canvas.set_tool(DeviceType.SWITCH)
    count = len(populated.plan.devices)
    QTest.mousePress(
        canvas.viewport(), Qt.MouseButton.RightButton, NO_MOD, vp(canvas, 900, 900)
    )
    QTest.mouseRelease(
        canvas.viewport(), Qt.MouseButton.RightButton, NO_MOD, vp(canvas, 900, 900)
    )
    assert len(populated.plan.devices) == count  # tools ignore right clicks
    canvas.set_tool(None)


def test_select_mode_press_falls_through(populated, canvas, scene):
    canvas.set_tool(None)
    QTest.mouseClick(canvas.viewport(), LEFT, NO_MOD, vp(canvas, 900, 900))


def test_place_device_with_armed_tool(populated, canvas, scene):
    canvas.set_tool(DeviceType.FIREWALL)
    count = len(populated.plan.devices)
    event = fake_press(scene, 800, 500)
    assert event.accepted
    assert len(populated.plan.devices) == count + 1
    assert populated.plan.devices[-1].device_type is DeviceType.FIREWALL
    canvas.set_tool(None)


def test_place_site_tool_accept_and_cancel(populated, canvas, scene):
    canvas.set_tool(SITE_TOOL)
    with patch(f"{CANVAS_NS}.SiteDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.SiteDialog.result_name", return_value="Annex"
    ), patch(f"{CANVAS_NS}.SiteDialog.result_notes", return_value=""), patch(
        f"{CANVAS_NS}.SiteDialog.result_color", return_value="#00838f"
    ):
        fake_press(scene, 900, 700)
    assert any(s.name == "Annex" for s in populated.plan.sites.values())

    count = len(populated.plan.sites)
    with patch(f"{CANVAS_NS}.SiteDialog.exec", return_value=0):
        fake_press(scene, 1300, 750)  # cancelled dialog places nothing
    assert len(populated.plan.sites) == count
    canvas.set_tool(None)


def test_place_textbox_tool_accept_blank_and_cancel(populated, canvas, scene):
    canvas.set_tool(TEXT_TOOL)
    with patch(f"{CANVAS_NS}.TextBoxDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_text", return_value="perimeter"
    ), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_font_size", return_value=12.0
    ), patch(f"{CANVAS_NS}.TextBoxDialog.result_bold", return_value=False), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_color", return_value="#1a1a1a"
    ), patch(f"{CANVAS_NS}.TextBoxDialog.result_width", return_value=180.0):
        fake_press(scene, 900, 820)
    assert any(t.text == "perimeter" for t in populated.plan.textboxes.values())

    count = len(populated.plan.textboxes)
    with patch(f"{CANVAS_NS}.TextBoxDialog.exec", return_value=1), patch(
        f"{CANVAS_NS}.TextBoxDialog.result_text", return_value="   "
    ):
        fake_press(scene, 940, 830)  # whitespace-only text is discarded
    with patch(f"{CANVAS_NS}.TextBoxDialog.exec", return_value=0):
        fake_press(scene, 960, 840)  # cancelled dialog places nothing
    assert len(populated.plan.textboxes) == count
    canvas.set_tool(None)


# ------------------------------------------------------------- connect mode
def choose_first_port(menu: QMenu, *args):
    return menu.actions()[0]


def test_connect_two_devices(populated, canvas, scene):
    canvas.set_tool(LinkType.ETHERNET)
    sw, rtr = populated.plan.devices[0], populated.plan.devices[1]
    count = len(populated.plan.links)
    with patch.object(QMenu, "exec", choose_first_port):
        fake_press(scene, sw.x, sw.y)
        assert scene._pending_source is not None
        fake_press(scene, rtr.x, rtr.y)
    assert len(populated.plan.links) == count + 1
    assert scene._pending_source is None
    canvas.set_tool(None)


def test_connect_cancel_paths(populated, canvas, scene):
    canvas.set_tool(LinkType.ETHERNET)
    sw = populated.plan.devices[0]
    count = len(populated.plan.links)

    # Cancelling the first port menu leaves nothing pending.
    with patch.object(QMenu, "exec", lambda self, *a: None):
        fake_press(scene, sw.x, sw.y)
    assert scene._pending_source is None

    # Picking a source then clicking empty canvas clears the pending pick.
    with patch.object(QMenu, "exec", choose_first_port):
        fake_press(scene, sw.x, sw.y)
    fake_press(scene, 900, 900)
    assert scene._pending_source is None

    # Picking a source then clicking the same device also clears it.
    with patch.object(QMenu, "exec", choose_first_port):
        fake_press(scene, sw.x, sw.y)
        fake_press(scene, sw.x, sw.y)
    assert scene._pending_source is None

    # Cancelling the second port menu keeps the source pending.
    rtr = populated.plan.devices[1]
    with patch.object(QMenu, "exec", choose_first_port):
        fake_press(scene, sw.x, sw.y)
    with patch.object(QMenu, "exec", lambda self, *a: None):
        fake_press(scene, rtr.x, rtr.y)
    assert scene._pending_source is not None
    scene._clear_pending()

    assert len(populated.plan.links) == count
    canvas.set_tool(None)


def test_pick_interface_special_cases(populated, canvas, scene):
    sw = populated.plan.devices[0]
    item = scene._device_items[sw.id]

    # No interfaces defined at all: untyped connection (returns None).
    saved = sw.interfaces
    sw.interfaces = []
    assert scene._pick_interface(item) is None
    sw.interfaces = saved

    # All ports in use: warning + cancelled.
    with patch.object(populated, "free_interfaces", return_value=[]), patch(
        f"{CANVAS_NS}.QMessageBox.warning"
    ) as warn:
        assert scene._pick_interface(item) is _CANCELLED
    warn.assert_called_once()


# ------------------------------------------------------------------ deletion
def test_delete_items_empty_selection_is_noop(populated, scene):
    scene.delete_items([])  # nothing selected: no prompt, no change


def test_delete_selection_confirmed_with_cascade(populated, scene):
    sw = populated.plan.devices[0]
    device_item = scene._device_items[sw.id]
    text_item = next(iter(scene._text_items.values()))
    site_item = next(iter(scene._site_items.values()))
    link_item = next(i for i in scene._link_items if isinstance(i, LinkItem))
    device_item.setSelected(True)
    text_item.setSelected(True)
    site_item.setSelected(True)
    link_item.setSelected(True)

    with patch(
        f"{CANVAS_NS}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as question:
        scene.delete_selection()
    message = question.call_args.args[2]
    assert "also remove" in message  # the second cable cascades

    assert populated.plan.get_device(sw.id) is None
    assert populated.plan.links == []
    assert populated.plan.textboxes == {}
    assert populated.plan.sites == {}


def test_delete_declined_keeps_everything(populated, scene):
    sw = populated.plan.devices[0]
    scene._device_items[sw.id].setSelected(True)
    with patch(
        f"{CANVAS_NS}.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        scene.delete_selection()
    assert populated.plan.get_device(sw.id) is sw


def test_delete_key_routes_to_selection(populated, scene):
    with patch.object(scene, "delete_selection") as ds:
        scene.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, NO_MOD)
        )
    ds.assert_called_once()


def test_other_key_passes_through(populated, scene):
    scene.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, NO_MOD)
    )  # unhandled key: falls through without raising


# ----------------------------------------------------------- double click add
def test_double_click_empty_canvas_adds_device(populated, canvas, scene):
    count = len(populated.plan.devices)
    with patch(f"{CANVAS_NS}.QInputDialog.getText", return_value=("cam-1", True)):
        QTest.mouseDClick(canvas.viewport(), LEFT, NO_MOD, vp(canvas, 900, 900))
    assert len(populated.plan.devices) == count + 1
    assert populated.plan.devices[-1].device_type is DeviceType.OTHER


def test_double_click_empty_canvas_cancelled(populated, canvas, scene):
    count = len(populated.plan.devices)
    with patch(f"{CANVAS_NS}.QInputDialog.getText", return_value=("", False)):
        QTest.mouseDClick(canvas.viewport(), LEFT, NO_MOD, vp(canvas, 950, 950))
    assert len(populated.plan.devices) == count


# ---------------------------------------------------------------- misc scene
def test_update_links_skips_missing_endpoints(populated, scene):
    # A link whose device items are absent is skipped without crashing.
    scene._device_items.clear()
    scene.update_links()
    scene.rebuild()


def test_plan_changed_signal_emitted_on_rebuild(populated, scene):
    fired = []
    scene.plan_changed.connect(lambda: fired.append(True))
    scene.rebuild()
    assert fired


# ------------------------------------------------------------- last branches
def test_site_notes_wrapping_branches(populated, scene):
    item = next(iter(scene._site_items.values()))

    # No notes at all: nothing to wrap.
    item.site.notes = ""
    assert item._notes_lines() == []

    # Enough text to exceed the line cap mid-paragraph and truncate.
    item.site.notes = ("annotation " * 40) + "\n" + ("overflow " * 40)
    lines = item._notes_lines()
    assert len(lines) <= SiteItem.NOTES_MAX_LINES
    assert lines[-1].endswith("…")
    render_scene(scene)


def test_site_body_drag_move_event_falls_through(populated, canvas, scene):
    item = next(iter(scene._site_items.values()))
    body = item.mapToScene(QPointF(8, item.site.height - 40))
    point = vp(canvas, body.x(), body.y())
    QTest.mousePress(canvas.viewport(), LEFT, NO_MOD, point)
    assert not item._resizing

    # A move with the button held while NOT resizing takes the super()
    # path (Qt's own item-drag machinery).
    target = body + QPointF(30, 20)
    move_point = canvas.mapFromScene(target)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(move_point),
        canvas.viewport().mapToGlobal(QPointF(move_point)),
        Qt.MouseButton.NoButton,
        LEFT,  # buttons still held: this is a drag
        NO_MOD,
    )
    QApplication.sendEvent(canvas.viewport(), event)
    QTest.mouseRelease(canvas.viewport(), LEFT, NO_MOD, move_point)
    populated.undo()
    scene.rebuild()


def test_canvas_delete_selection_delegates(populated, canvas, scene):
    with patch.object(scene, "delete_selection") as ds:
        canvas.delete_selection()
    ds.assert_called_once()
