"""Startup and MainWindow handler coverage.

main() is run with a stub QApplication whose exec() returns at once
(Qt allows only one real QApplication, and the suite already owns it).
MainWindow handlers are invoked directly with the static Qt dialogs
patched, so every menu path runs headlessly without blocking.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtCore import (
    QEvent,
    Qt,
)
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from netplanner.app.controller import AppController
from netplanner.domain.entities import DeviceType, LinkType
from netplanner.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def window(app):
    controller = AppController(repository=MagicMock())
    controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    win = MainWindow(controller)
    yield win
    win.close()


# ------------------------------------------------------------------- main()
def test_main_entry_point(app, monkeypatch, tmp_path):
    import PyQt6.QtWidgets as qtw

    import netplanner.main as main_mod

    # Keep the real app's on-disk state out of the user's home directory.
    monkeypatch.setenv("NETPLANNER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    fake_app = MagicMock()
    fake_app.exec.return_value = 0
    monkeypatch.setattr(qtw, "QApplication", MagicMock(return_value=fake_app))
    assert main_mod.main() == 0
    fake_app.exec.assert_called_once()


# ----------------------------------------------------------------- handlers
def test_new_plan_and_title(window):
    window._new_plan()
    assert "Untitled plan" in window.windowTitle()


def test_save_shows_status(window):
    window._save()
    window.controller.repository.save.assert_called_once()
    assert "saved" in window.statusBar().currentMessage().lower()


def test_delete_routes_to_canvas(window):
    with patch.object(window.canvas, "delete_selection") as ds:
        window._delete()
    ds.assert_called_once()


def test_rename_plan_accepted(window):
    with patch(
        "netplanner.gui.main_window.QInputDialog.getText",
        return_value=("Campus core", True),
    ):
        window._rename_plan()
    assert window.controller.plan.name == "Campus core"
    assert "Campus core" in window.windowTitle()


def test_rename_plan_cancelled(window):
    original = window.controller.plan.name
    with patch(
        "netplanner.gui.main_window.QInputDialog.getText",
        return_value=("ignored", False),
    ):
        window._rename_plan()
    assert window.controller.plan.name == original


def test_undo_redo_handlers(window):
    count = len(window.controller.plan.devices)
    window.controller.add_device("tmp", DeviceType.ROUTER, 10, 10)
    window._undo()
    assert len(window.controller.plan.devices) == count
    window._redo()
    assert len(window.controller.plan.devices) == count + 1


def test_auto_layout_handler(window):
    a = window.controller.add_device("a2", DeviceType.ROUTER, 5, 5)
    window.controller.add_link(
        window.controller.plan.devices[0].id, a.id, LinkType.ETHERNET
    )
    window._auto_layout()  # positions recomputed without raising


def test_validate_no_issues(window):
    with patch.object(window.controller, "validate_plan", return_value=[]), patch(
        "netplanner.gui.main_window.QMessageBox.information"
    ) as info:
        window._validate()
    info.assert_called_once()


def test_validate_with_issues(window):
    # An isolated second device guarantees at least one warning.
    window.controller.add_device("lonely", DeviceType.SERVER, 900, 900)
    with patch("netplanner.gui.main_window.QMessageBox.warning") as warn:
        window._validate()
    warn.assert_called_once()


def test_export_pdf_handler(window, tmp_path):
    target = tmp_path / "plan.pdf"
    with patch(
        "netplanner.gui.main_window.QFileDialog.getSaveFileName",
        return_value=(str(target), "PDF files (*.pdf)"),
    ):
        window._export_pdf()
    assert target.exists()
    assert str(target) in window.statusBar().currentMessage()


def test_export_pdf_cancelled(window):
    with patch(
        "netplanner.gui.main_window.QFileDialog.getSaveFileName",
        return_value=("", ""),
    ), patch.object(window.controller, "export_to_pdf") as export:
        window._export_pdf()
    export.assert_not_called()


def test_export_png_handler(window, tmp_path):
    target = tmp_path / "plan.png"
    with patch(
        "netplanner.gui.main_window.QFileDialog.getSaveFileName",
        return_value=(str(target), "PNG files (*.png)"),
    ):
        window._export_png()
    assert target.exists()


def test_export_png_cancelled(window):
    with patch(
        "netplanner.gui.main_window.QFileDialog.getSaveFileName",
        return_value=("", ""),
    ), patch.object(window.controller, "export_to_png") as export:
        window._export_png()
    export.assert_not_called()


def test_guarded_slot_reports_errors(window):
    def exploding():
        raise RuntimeError("kaboom")

    wrapped = window._guarded(exploding)
    with patch("netplanner.gui.main_window.QMessageBox.critical") as crit:
        wrapped()  # swallowed, reported, no crash
    crit.assert_called_once()
    assert "kaboom" in crit.call_args.args[2]


def test_canvas_escape_resets_palette(window):
    window.canvas.set_tool(DeviceType.SWITCH)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    window.canvas.keyPressEvent(event)
    assert window.canvas._scene.armed_tool is None


def test_canvas_escape_without_palette(app):
    from netplanner.gui.canvas import NetworkCanvas

    controller = AppController(repository=MagicMock())
    canvas = NetworkCanvas(controller)  # no MainWindow, so no palette_dock
    canvas.set_tool(LinkType.FIBER)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    canvas.keyPressEvent(event)
    assert canvas._scene.armed_tool is None
    canvas.deleteLater()


def test_canvas_other_key_passthrough(window):
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    window.canvas.keyPressEvent(event)  # falls through to Qt without raising


@pytest.mark.filterwarnings("ignore::RuntimeWarning")  # runpy re-execution notice
def test_main_module_dunder_guard(app, monkeypatch, tmp_path):
    """Running the module as __main__ raises SystemExit(main())."""
    import runpy

    import PyQt6.QtWidgets as qtw

    monkeypatch.setenv("NETPLANNER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    fake_app = MagicMock()
    fake_app.exec.return_value = 0
    monkeypatch.setattr(qtw, "QApplication", MagicMock(return_value=fake_app))
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("netplanner.main", run_name="__main__")
    assert excinfo.value.code == 0
