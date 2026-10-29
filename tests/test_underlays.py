"""Underlays: PDFs and images placed behind the diagram.

Covers each layer the feature passes through: the entity and its
commands, persistence in both stores, the scene bounds and both
exporters, the Qt importer (PDF pages via QtPdf, images via
QImageReader), the canvas item, and the File menu entry.

Test PDFs are generated with reportlab and test images with Qt, so no
binary fixtures live in the repository.
"""

from __future__ import annotations

import base64
import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QMenu, QMessageBox
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas as pdf_canvas

import netplanner.gui.underlay_import as underlay_import
from netplanner.app.commands import (
    DeleteUnderlayCommand,
    EditUnderlayCommand,
    SetUnderlayGeometryCommand,
)
from netplanner.app.controller import AppController
from netplanner.domain.entities import UNDERLAY_MAX_SIDE_PX, DeviceType, Underlay
from netplanner.domain.model import NetworkPlan
from netplanner.errors import ExportError, PersistenceError, UnderlayImportError
from netplanner.export.pdf_exporter import export_pdf
from netplanner.export.png_exporter import export_png
from netplanner.export.renderer import MARGIN, UnderlayShape, build_scene
from netplanner.export.underlays import faded_underlay
from netplanner.gui.canvas import NetworkCanvas, UnderlayItem
from netplanner.gui.main_window import MainWindow
from netplanner.gui.underlay_import import is_pdf, load_underlay
from netplanner.persistence.project_file import load_project, save_project
from netplanner.persistence.repository import PlanRepository

CANVAS_NS = "netplanner.gui.canvas"
LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier


# ------------------------------------------------------------------ helpers
@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def png_bytes(width: int = 40, height: int = 20, color: str = "#336699") -> bytes:
    """A solid-color PNG, encoded with Pillow so no Qt app is needed."""
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def make_pdf(path, pages: int = 1, encrypt: str | None = None):
    kwargs = {"pagesize": (600, 300)}
    if encrypt:
        kwargs["encrypt"] = StandardEncryption(encrypt, canPrint=0)
    c = pdf_canvas.Canvas(str(path), **kwargs)
    for n in range(pages):
        c.drawString(50, 150, f"page {n + 1}")
        c.showPage()
    c.save()
    return path


def make_underlay(**kwargs) -> Underlay:
    kwargs.setdefault("image_png_b64", base64.b64encode(png_bytes()).decode())
    return Underlay(**kwargs)


class FakeSceneEvent:
    """Stub for scene-event branches that never reach super()."""

    def __init__(self, scene_pos=None, item_pos=None):
        self._scene_pos = scene_pos or QPointF()
        self._item_pos = item_pos or QPointF()
        self.accepted = False

    def scenePos(self):
        return self._scene_pos

    def pos(self):
        return self._item_pos

    def screenPos(self):
        from PyQt6.QtCore import QPoint

        return QPoint(10, 10)

    def accept(self):
        self.accepted = True


def pick(text: str):
    """A QMenu.exec stand-in choosing the action labelled `text`."""
    return lambda menu, *a: next(x for x in menu.actions() if x.text() == text)


# ------------------------------------------------------- domain + commands
def test_underlay_decodes_its_picture():
    data = png_bytes()
    assert make_underlay(image_png_b64=base64.b64encode(data).decode()).image_png == data


def test_plan_adds_finds_and_removes_underlays():
    plan = NetworkPlan()
    underlay = plan.add_underlay(make_underlay())
    assert plan.get_underlay(underlay.id) is underlay
    plan.remove_underlay(underlay.id)
    plan.remove_underlay(underlay.id)  # a second removal is a no-op
    assert plan.get_underlay(underlay.id) is None


def test_controller_underlay_lifecycle_is_undoable():
    ctrl = AppController(repository=MagicMock())
    data = png_bytes()
    underlay = ctrl.add_underlay("plan.pdf", data, 10, 20, 300, 150)
    assert underlay.image_png == data and underlay.locked
    assert ctrl.plan.underlays == {underlay.id: underlay}

    ctrl.set_underlay_geometry(underlay.id, 50, 60, 300, 150)
    assert ctrl.commands._undo[-1].description == "Move underlay"
    ctrl.set_underlay_geometry(underlay.id, 50, 60, 600, 300)
    assert ctrl.commands._undo[-1].description == "Resize underlay"
    ctrl.undo()
    assert (underlay.width, underlay.height) == (300, 150)

    ctrl.edit_underlay(underlay.id, 0.0, False)
    assert underlay.opacity == 0.05  # clamped: an invisible underlay is lost
    assert not underlay.locked
    ctrl.edit_underlay(underlay.id, 3.0, True)
    assert underlay.opacity == 1.0
    ctrl.undo()
    assert (underlay.opacity, underlay.locked) == (0.05, False)

    ctrl.delete_underlay(underlay.id)
    assert ctrl.plan.underlays == {}
    assert ctrl.commands._undo[-1].description == "Delete underlay 'plan.pdf'"
    ctrl.undo()
    assert ctrl.plan.underlays == {underlay.id: underlay}
    ctrl.undo()
    ctrl.undo()
    ctrl.undo()
    ctrl.undo()  # back past the add
    assert ctrl.plan.underlays == {}


def test_underlay_commands_on_a_missing_id_do_nothing():
    plan = NetworkPlan()
    for command in (
        SetUnderlayGeometryCommand(plan, "gone", 1, 2, 3, 4),
        EditUnderlayCommand(plan, "gone", 0.5, False),
        DeleteUnderlayCommand(plan, "gone"),
    ):
        command.execute()
        command.undo()
    assert plan.underlays == {}
    assert DeleteUnderlayCommand(plan, "gone").description == "Delete underlay"


def test_an_unnamed_underlay_is_described_as_untitled():
    ctrl = AppController(repository=MagicMock())
    ctrl.add_underlay("", png_bytes(), 0, 0, 10, 10)
    assert ctrl.commands._undo[-1].description == "Add underlay 'untitled'"


# -------------------------------------------------------------- persistence
def test_underlays_survive_the_database(tmp_path):
    plan = NetworkPlan()
    underlay = plan.add_underlay(make_underlay(name="f1.png", opacity=0.3, locked=False))
    repo = PlanRepository(tmp_path / "plans.db")
    repo.save(plan)
    loaded = repo.load(plan.id).get_underlay(underlay.id)
    assert loaded == underlay


def test_underlays_survive_a_project_file(tmp_path):
    plan = NetworkPlan()
    underlay = plan.add_underlay(make_underlay(name="f1.png", x=-40))
    save_project(plan, tmp_path / "p.netplan")
    assert load_project(tmp_path / "p.netplan").get_underlay(underlay.id) == underlay


def test_a_project_file_from_before_underlays_still_opens(tmp_path):
    path = tmp_path / "old.netplan"
    path.write_text(json.dumps({"format": "netplan", "name": "old"}))
    assert load_project(path).underlays == {}


def test_a_plan_too_large_to_reopen_is_not_written(tmp_path, monkeypatch):
    """Writing a file load_project would refuse is worse than refusing to
    write it: the failure would surface on the recipient's machine."""
    import netplanner.persistence.project_file as project_file

    monkeypatch.setattr(project_file, "MAX_PROJECT_BYTES", 1000)
    plan = NetworkPlan()
    plan.add_underlay(make_underlay(image_png_b64="A" * 2000))
    target = tmp_path / "big.netplan"
    with pytest.raises(PersistenceError, match="remove an underlay"):
        save_project(plan, target)
    assert not target.exists()


# ------------------------------------------------------------------- scene
def test_underlay_takes_part_in_the_page_bounds():
    plan = NetworkPlan()
    plan.add_underlay(make_underlay(x=-500, y=-300, width=2000, height=1000))
    ctrl = AppController(repository=MagicMock())
    ctrl.plan = plan
    ctrl.add_device("sw1", DeviceType.SWITCH, 0, 0)  # well inside the drawing
    scene = build_scene(plan)
    shape = scene.underlays[0]
    assert (shape.x, shape.y) == (MARGIN, MARGIN)
    assert scene.width == 2000 + 2 * MARGIN
    assert scene.height == 1000 + 2 * MARGIN


def test_a_plan_holding_only_an_underlay_exports_it():
    plan = NetworkPlan()
    plan.add_underlay(make_underlay(x=100, y=100, width=800, height=400))
    scene = build_scene(plan)
    assert len(scene.underlays) == 1 and scene.width == 800 + 2 * MARGIN


def test_a_plan_holding_only_a_site_now_exports_it():
    from netplanner.domain.entities import Site

    plan = NetworkPlan()
    plan.add_site(Site(name="MDF", x=0, y=0, width=300, height=200))
    assert len(build_scene(plan).sites) == 1


# ---------------------------------------------------------------- exporters
def _shape(**kwargs) -> UnderlayShape:
    fields: dict = {
        "x": 0, "y": 0, "width": 40, "height": 20,
        "image_png_b64": base64.b64encode(png_bytes()).decode(), "opacity": 1.0,
    }
    return UnderlayShape(**(fields | kwargs))


def test_fade_blends_toward_the_page():
    assert faded_underlay(_shape(opacity=1.0)).getpixel((5, 5)) == (0x33, 0x66, 0x99)
    faded = faded_underlay(_shape(opacity=0.5)).getpixel((5, 5))
    assert faded == pytest.approx((0x99, 0xB2, 0xCC), abs=1)


def test_fade_fills_transparency_with_the_page_color():
    out = io.BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(out, "PNG")
    shape = _shape(image_png_b64=base64.b64encode(out.getvalue()).decode())
    assert faded_underlay(shape).getpixel((1, 1)) == (255, 255, 255)


def test_fade_resizes_only_when_asked():
    assert faded_underlay(_shape(), (40, 20)).size == (40, 20)
    assert faded_underlay(_shape(), (80, 40)).size == (80, 40)


def test_fade_refuses_a_picture_no_import_could_have_made(monkeypatch):
    import netplanner.export.underlays as underlays

    monkeypatch.setattr(underlays, "MAX_UNDERLAY_PIXELS", 100)
    with pytest.raises(ValueError, match="over the"):
        faded_underlay(_shape())


def _plan_with_underlay_and_site() -> NetworkPlan:
    ctrl = AppController(repository=MagicMock())
    ctrl.add_underlay("f.png", png_bytes(200, 100), -100, -100, 800, 400)
    ctrl.add_site("MDF", 0, 0)
    ctrl.add_device("sw1", DeviceType.SWITCH, 100, 100)
    return ctrl.plan


def test_png_export_draws_the_underlay_beneath_a_translucent_site(tmp_path):
    plan = _plan_with_underlay_and_site()
    export_png(plan, tmp_path / "out.png")
    image = Image.open(tmp_path / "out.png").convert("RGB")
    # Top-left of the underlay, outside the site: the faded picture.
    assert image.getpixel((MARGIN + 10, 40 + MARGIN + 10)) == pytest.approx(
        (0x99, 0xB2, 0xCC), abs=2
    )
    # Inside the site's body, the picture still shows through the tint.
    site_body = image.getpixel((MARGIN + 100 + 10, 40 + MARGIN + 100 + 200))
    assert site_body != (255, 255, 255)
    assert site_body[2] > site_body[0]  # still the picture's blue cast


def test_pdf_export_draws_the_underlay(tmp_path):
    plan = _plan_with_underlay_and_site()
    export_pdf(plan, tmp_path / "out.pdf")
    assert b"/Subtype /Image" in (tmp_path / "out.pdf").read_bytes()


def test_a_damaged_underlay_fails_export_with_context(tmp_path):
    plan = NetworkPlan(name="broken")
    plan.add_underlay(make_underlay(image_png_b64="not base64!"))
    with pytest.raises(ExportError, match="broken"):
        export_png(plan, tmp_path / "out.png")
    with pytest.raises(ExportError, match="broken"):
        export_pdf(plan, tmp_path / "out.pdf")


# ----------------------------------------------------------------- importer
def write_qimage(path, width: int, height: int, fmt: str = "PNG"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc3300"))
    assert image.save(str(path), fmt)
    return path


def test_is_pdf_reads_the_signature_not_the_name(app, tmp_path):
    assert is_pdf(make_pdf(tmp_path / "drawing.bin"))
    assert not is_pdf(write_qimage(tmp_path / "fake.pdf", 4, 4))
    with pytest.raises(UnderlayImportError, match="Could not read"):
        is_pdf(tmp_path / "missing.pdf")


def test_a_single_page_pdf_imports_without_asking(app, tmp_path):
    chooser = MagicMock()
    picture = load_underlay(make_pdf(tmp_path / "floor.pdf"), chooser)
    chooser.assert_not_called()
    assert picture.name == "floor.pdf"
    assert picture.png.startswith(b"\x89PNG")
    assert picture.width == underlay_import.DEFAULT_LONGEST_SIDE
    assert picture.height == pytest.approx(picture.width / 2, abs=2)  # 600x300 page


def test_a_multi_page_pdf_asks_which_page(app, tmp_path):
    path = make_pdf(tmp_path / "set.pdf", pages=3)
    chooser = MagicMock(return_value=2)
    assert load_underlay(path, chooser) is not None
    chooser.assert_called_once_with(3)
    assert load_underlay(path, lambda count: None) is None  # cancelled


def test_a_password_protected_pdf_is_refused(app, tmp_path):
    path = make_pdf(tmp_path / "locked.pdf", encrypt="secret")
    with pytest.raises(UnderlayImportError, match="password-protected"):
        load_underlay(path, lambda n: 0)


def test_a_corrupt_pdf_is_refused(app, tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not a pdf body")
    with pytest.raises(UnderlayImportError, match="could not be opened"):
        load_underlay(path, lambda n: 0)


def test_a_page_that_will_not_render_is_reported(app, tmp_path, monkeypatch):
    from PyQt6.QtPdf import QPdfDocument

    monkeypatch.setattr(QPdfDocument, "render", lambda self, page, size: QImage())
    with pytest.raises(UnderlayImportError, match="could not be rendered"):
        load_underlay(make_pdf(tmp_path / "floor.pdf"), lambda n: 0)


def test_an_image_imports_at_its_aspect_ratio(app, tmp_path):
    picture = load_underlay(write_qimage(tmp_path / "plan.jpg", 300, 100, "JPG"), MagicMock())
    assert picture.name == "plan.jpg"
    assert picture.width == underlay_import.DEFAULT_LONGEST_SIDE
    assert picture.height == pytest.approx(underlay_import.DEFAULT_LONGEST_SIDE / 3)
    stored = QImage.fromData(picture.png)
    assert (stored.width(), stored.height()) == (300, 100)


def test_an_oversized_image_is_scaled_down_on_import(app, tmp_path):
    path = write_qimage(tmp_path / "huge.png", UNDERLAY_MAX_SIDE_PX * 2, 64)
    stored = QImage.fromData(load_underlay(path, MagicMock()).png)
    assert stored.width() == UNDERLAY_MAX_SIDE_PX
    assert stored.height() == 32


def test_a_non_image_is_refused(app, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("just text")
    with pytest.raises(UnderlayImportError, match="not a PDF or an image"):
        load_underlay(path, MagicMock())


def test_an_image_that_will_not_decode_is_reported(app, tmp_path, monkeypatch):
    from PyQt6.QtGui import QImageReader

    monkeypatch.setattr(QImageReader, "read", lambda self: QImage())
    with pytest.raises(UnderlayImportError, match="Could not decode"):
        load_underlay(write_qimage(tmp_path / "a.png", 8, 8), MagicMock())


def test_a_missing_file_is_reported(app, tmp_path):
    with pytest.raises(UnderlayImportError, match="Could not read"):
        load_underlay(tmp_path / "gone.png", MagicMock())


def test_an_oversized_file_is_refused_before_it_is_read(app, tmp_path, monkeypatch):
    monkeypatch.setattr(underlay_import, "MAX_UNDERLAY_FILE_BYTES", 10)
    with pytest.raises(UnderlayImportError, match="limit for an underlay"):
        load_underlay(write_qimage(tmp_path / "a.png", 8, 8), MagicMock())


def test_a_png_over_the_stored_limit_is_scaled_until_it_fits(app, tmp_path, monkeypatch):
    # Noise compresses badly, so the first encode is well over the limit.
    noise = Image.effect_noise((400, 400), 100).convert("RGB")
    path = tmp_path / "noise.png"
    noise.save(path)
    monkeypatch.setattr(underlay_import, "MAX_UNDERLAY_PNG_BYTES", 60_000)
    picture = load_underlay(path, MagicMock())
    assert len(picture.png) <= 60_000
    assert QImage.fromData(picture.png).width() < 400


def test_import_strips_image_metadata(app, tmp_path):
    exif = Image.Exif()
    exif[0x010E] = "GPS secret description"  # ImageDescription
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), "white").save(path, exif=exif.tobytes())
    assert b"GPS secret" in path.read_bytes()
    assert b"GPS secret" not in load_underlay(path, MagicMock()).png


# -------------------------------------------------------------------- canvas
@pytest.fixture()
def controller():
    return AppController(repository=MagicMock())


@pytest.fixture()
def canvas(app, controller):
    view = NetworkCanvas(controller)
    view.resize(1000, 800)
    view.show()
    yield view
    view.close()
    view.deleteLater()


@pytest.fixture()
def scene(canvas):
    return canvas._scene


@pytest.fixture()
def underlaid(controller, scene):
    controller.add_underlay("floor.png", png_bytes(), 0, 0, 800, 400)
    scene.rebuild()
    return controller


def item_of(scene) -> UnderlayItem:
    return next(iter(scene._underlay_items.values()))


def render(scene) -> None:
    image = QImage(1200, 800, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    scene.render(painter)
    painter.end()


def unlock(controller, scene) -> UnderlayItem:
    underlay = next(iter(controller.plan.underlays.values()))
    controller.edit_underlay(underlay.id, underlay.opacity, False)
    scene.rebuild()
    return item_of(scene)


def vp(canvas, x, y):
    canvas.centerOn(QPointF(x, y))
    return canvas.mapFromScene(QPointF(x, y))


def test_a_locked_underlay_is_inert(underlaid, scene):
    item = item_of(scene)
    assert item.zValue() < -20  # behind sites
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    render(scene)


def test_an_unlocked_underlay_moves_and_paints_its_outline(underlaid, scene):
    item = unlock(underlaid, scene)
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    render(scene)
    item.setSelected(True)
    render(scene)  # selected: thicker outline and the grip


def test_a_damaged_picture_paints_a_placeholder(controller, scene):
    controller.plan.add_underlay(make_underlay(image_png_b64="!!!", name="bad.png"))
    scene.rebuild()
    assert item_of(scene).pixmap.isNull()
    render(scene)


def test_pictures_are_decoded_once_and_dropped_with_their_underlay(underlaid, scene):
    first = item_of(scene).pixmap
    scene.rebuild()
    assert item_of(scene).pixmap is first
    underlaid.delete_underlay(item_of(scene).underlay.id)
    scene.rebuild()
    assert scene._pixmaps == {}


def test_clicks_go_through_a_locked_underlay(underlaid, canvas, scene):
    canvas.set_tool(DeviceType.ROUTER)
    QTest.mouseClick(canvas.viewport(), LEFT, NO_MOD, vp(canvas, 200, 200))
    assert len(underlaid.plan.devices) == 1
    canvas.set_tool(None)


def test_double_click_on_an_underlay_adds_a_device(underlaid, canvas, scene):
    with patch(f"{CANVAS_NS}.QInputDialog.getText", return_value=("ap1", True)):
        QTest.mouseDClick(canvas.viewport(), LEFT, NO_MOD, vp(canvas, 300, 200))
    assert [d.name for d in underlaid.plan.devices] == ["ap1"]


def test_item_at_still_finds_items_above_an_underlay(underlaid, scene):
    device = underlaid.add_device("sw1", DeviceType.SWITCH, 200, 200)
    scene.rebuild()
    assert scene._item_at(QPointF(200, 200)) is scene._device_items[device.id]


def test_resize_keeps_the_aspect_ratio(underlaid, scene):
    item = unlock(underlaid, scene)
    press = FakeSceneEvent(QPointF(795, 395), QPointF(795, 395))
    item.mousePressEvent(press)
    assert press.accepted and item._resizing
    item.mouseMoveEvent(FakeSceneEvent(QPointF(1195, 9999)))
    assert (item.underlay.width, item.underlay.height) == (1200, 600)
    item.mouseMoveEvent(FakeSceneEvent(QPointF(-9999, 0)))
    assert item.underlay.width == UnderlayItem.MIN_W
    assert item.underlay.height == UnderlayItem.MIN_W / 2
    release = FakeSceneEvent()
    item.mouseReleaseEvent(release)
    assert release.accepted and not item._resizing
    assert underlaid.commands._undo[-1].description == "Resize underlay"
    underlaid.undo()
    assert (item.underlay.width, item.underlay.height) == (800, 400)


def test_dragging_the_body_commits_one_move(underlaid, canvas, scene):
    item = unlock(underlaid, scene)
    point = vp(canvas, 100, 100)
    QTest.mousePress(canvas.viewport(), LEFT, NO_MOD, point)
    assert not item._resizing
    item.setPos(130, 140)
    QTest.mouseRelease(canvas.viewport(), LEFT, NO_MOD, point)
    assert (item.underlay.x, item.underlay.y) == (130, 140)


def test_a_click_without_movement_commits_nothing(underlaid, scene):
    """Qt refuses to build scene mouse events from Python, so the base
    class handlers are stubbed and the item's own logic runs alone."""
    item = unlock(underlaid, scene)
    depth = len(underlaid.commands._undo)
    with patch.object(QGraphicsItem, "mouseMoveEvent") as base_move, patch.object(
        QGraphicsItem, "mouseReleaseEvent"
    ):
        item.mouseMoveEvent(FakeSceneEvent())  # not resizing: Qt moves it
        item.mouseReleaseEvent(FakeSceneEvent())
    base_move.assert_called_once()
    assert len(underlaid.commands._undo) == depth


def test_hovering_the_grip_shows_the_resize_cursor(underlaid, scene):
    item = unlock(underlaid, scene)
    with patch.object(QGraphicsItem, "hoverMoveEvent"), patch.object(
        QGraphicsItem, "hoverLeaveEvent"
    ):
        item.hoverMoveEvent(FakeSceneEvent(item_pos=QPointF(795, 395)))
        assert item._hovered_grip
        assert item.cursor().shape() == Qt.CursorShape.SizeFDiagCursor
        item.hoverMoveEvent(FakeSceneEvent(item_pos=QPointF(795, 395)))  # unchanged
        render(scene)
        item.hoverMoveEvent(FakeSceneEvent(item_pos=QPointF(10, 10)))
        assert not item._hovered_grip
        item._hovered_grip = True
        item.hoverLeaveEvent(FakeSceneEvent())
        assert not item._hovered_grip


def test_context_menu_unlocks_and_locks(underlaid, scene):
    with patch.object(QMenu, "exec", pick("Unlock underlay (move / resize)")):
        item_of(scene).contextMenuEvent(FakeSceneEvent())
    assert not next(iter(underlaid.plan.underlays.values())).locked
    assert item_of(scene).flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    with patch.object(QMenu, "exec", pick("Lock underlay")):
        item_of(scene).contextMenuEvent(FakeSceneEvent())
    assert next(iter(underlaid.plan.underlays.values())).locked


def test_context_menu_sets_opacity(underlaid, scene):
    item = item_of(scene)
    with patch.object(QMenu, "exec", pick("Underlay opacity…")), patch(
        f"{CANVAS_NS}.QInputDialog.getInt", return_value=(25, True)
    ):
        item.contextMenuEvent(FakeSceneEvent())
    assert item.underlay.opacity == 0.25
    with patch.object(QMenu, "exec", pick("Underlay opacity…")), patch(
        f"{CANVAS_NS}.QInputDialog.getInt", return_value=(90, False)
    ):
        item.contextMenuEvent(FakeSceneEvent())
    assert item.underlay.opacity == 0.25


def test_context_menu_deletes_after_confirming(underlaid, scene):
    with patch.object(QMenu, "exec", pick("Delete underlay")), patch(
        f"{CANVAS_NS}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
    ) as question:
        item_of(scene).contextMenuEvent(FakeSceneEvent())
    assert "1 underlay(s)" in question.call_args.args[2]
    assert underlaid.plan.underlays == {}


def test_a_detached_underlay_menu_touches_no_scene(app, controller):
    underlay = controller.add_underlay("f.png", png_bytes(), 0, 0, 100, 50)
    item = UnderlayItem(underlay, QPixmap(), controller)
    for label in ("Unlock underlay (move / resize)", "Delete underlay"):
        with patch.object(QMenu, "exec", pick(label)):
            item.contextMenuEvent(FakeSceneEvent())
    with patch.object(QMenu, "exec", lambda menu, *a: None):
        item.contextMenuEvent(FakeSceneEvent())
    assert controller.plan.underlays == {underlay.id: underlay}  # never deleted


def test_paint_without_a_painter_is_a_no_op(underlaid, scene):
    item_of(scene).paint(None, None)


# -------------------------------------------------------------- main window
@pytest.fixture()
def window(app):
    win = MainWindow(AppController(repository=MagicMock()))
    yield win
    win.close()


def test_import_underlay_menu_places_it_at_the_view_center(window, tmp_path):
    path = write_qimage(tmp_path / "site.png", 200, 100)
    with patch(
        "netplanner.gui.main_window.QFileDialog.getOpenFileName",
        return_value=(str(path), ""),
    ):
        window._import_underlay()
    underlay = next(iter(window.controller.plan.underlays.values()))
    center = window.canvas.visible_center()
    assert underlay.x + underlay.width / 2 == pytest.approx(center.x())
    assert underlay.y + underlay.height / 2 == pytest.approx(center.y())
    assert window.canvas._scene._underlay_items


def test_import_underlay_dialog_cancelled(window):
    with patch(
        "netplanner.gui.main_window.QFileDialog.getOpenFileName", return_value=("", "")
    ):
        window._import_underlay()
    assert window.controller.plan.underlays == {}


def test_import_underlay_page_choice_cancelled(window, tmp_path):
    path = make_pdf(tmp_path / "set.pdf", pages=2)
    with patch(
        "netplanner.gui.main_window.QFileDialog.getOpenFileName",
        return_value=(str(path), ""),
    ), patch("netplanner.gui.main_window.QInputDialog.getInt", return_value=(1, False)):
        window._import_underlay()
    assert window.controller.plan.underlays == {}


def test_choose_pdf_page_is_one_based_on_screen(window):
    with patch(
        "netplanner.gui.main_window.QInputDialog.getInt", return_value=(3, True)
    ) as ask:
        assert window._choose_pdf_page(4) == 2
    assert ask.call_args.args[3:] == (1, 1, 4)


def test_png_round_trip_through_qbuffer_matches_pillow(app):
    """The importer's PNGs are read back by Pillow in the exporters."""
    image = QImage(10, 5, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    decoded = Image.open(io.BytesIO(bytes(buffer.data().data())))
    assert decoded.size == (10, 5)


# ------------------------------------------- reaching a locked underlay
def test_missing_pdf_libraries_cost_only_pdf_import(app, tmp_path, monkeypatch):
    """QtPdf is imported on use, so a system lacking its libraries still
    starts; only a PDF import fails, and it says why."""
    import sys

    monkeypatch.setitem(sys.modules, "PyQt6.QtPdf", None)  # import raises
    with pytest.raises(UnderlayImportError, match="PDF support is unavailable"):
        load_underlay(make_pdf(tmp_path / "floor.pdf"), lambda n: 0)
    # Images do not touch QtPdf at all.
    assert load_underlay(write_qimage(tmp_path / "a.png", 8, 8), MagicMock())


def test_lock_and_unlock_all_is_one_undo_step():
    ctrl = AppController(repository=MagicMock())
    first = ctrl.add_underlay("a.png", png_bytes(), 0, 0, 10, 10)
    second = ctrl.add_underlay("b.png", png_bytes(), 0, 0, 10, 10)
    ctrl.edit_underlay(second.id, second.opacity, False)

    ctrl.set_underlays_locked(False)
    assert not first.locked and not second.locked
    assert ctrl.commands._undo[-1].description == "Unlock underlays"
    ctrl.undo()
    assert first.locked and not second.locked  # each back to its own state

    ctrl.set_underlays_locked(True)
    assert ctrl.commands._undo[-1].description == "Lock underlays"
    ctrl.plan.remove_underlay(first.id)  # gone before the undo
    ctrl.undo()
    assert not second.locked


def test_plan_menu_unlocks_every_underlay(window):
    window.controller.add_underlay("a.png", png_bytes(), 0, 0, 100, 50)
    window._refresh_all()
    window._set_underlays_locked(False)
    item = next(iter(window.canvas._scene._underlay_items.values()))
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    window._set_underlays_locked(True)
    item = next(iter(window.canvas._scene._underlay_items.values()))
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton


def test_a_real_right_click_reaches_a_locked_underlay(underlaid, canvas, scene):
    """The other tests call contextMenuEvent directly; this proves Qt
    still routes a right-click to an item that accepts no mouse buttons."""
    from PyQt6.QtGui import QContextMenuEvent

    point = vp(canvas, 400, 200)
    seen = []

    def record(menu, *args):
        seen.extend(a.text() for a in menu.actions())

    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, point, canvas.viewport().mapToGlobal(point)
    )
    with patch.object(QMenu, "exec", record):
        QApplication.sendEvent(canvas.viewport(), event)
    assert "Unlock underlay (move / resize)" in seen
