"""Main application window (PyQt6)."""

from __future__ import annotations

import functools
import traceback
from pathlib import Path

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from netplanner.app.controller import AppController

from .canvas import NetworkCanvas
from .palette import EquipmentPalette
from .panels import PropertiesPanel


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("NetPlanner")
        self.resize(1200, 800)

        self.canvas = NetworkCanvas(controller, self)
        self.setCentralWidget(self.canvas)

        self.palette_dock = EquipmentPalette(self)
        self.palette_dock.tool_changed.connect(self.canvas.set_tool)
        self.addDockWidget(self.palette_dock.preferred_area(), self.palette_dock)

        self.properties_panel = PropertiesPanel(controller, self)
        self.addDockWidget(
            self.properties_panel.preferred_area(), self.properties_panel
        )

        self._build_menus()

    # ----------------------------------------------------------------- menus
    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self._action("&New plan", QKeySequence.StandardKey.New, self._new_plan))
        file_menu.addAction(self._action("&Save", QKeySequence.StandardKey.Save, self._save))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Export &PDF…", None, self._export_pdf))
        file_menu.addAction(self._action("Export P&NG…", None, self._export_png))
        file_menu.addSeparator()
        file_menu.addAction(self._action("&Quit", QKeySequence.StandardKey.Quit, self.close))

        edit_menu = bar.addMenu("&Edit")
        edit_menu.addAction(self._action("&Undo", QKeySequence.StandardKey.Undo, self._undo))
        edit_menu.addAction(self._action("&Redo", QKeySequence.StandardKey.Redo, self._redo))
        edit_menu.addSeparator()
        edit_menu.addAction(
            self._action("&Delete selected", QKeySequence.StandardKey.Delete, self._delete)
        )

        view_menu = bar.addMenu("&View")
        details_action = QAction("Show device &details", self)
        details_action.setCheckable(True)
        details_action.setChecked(True)  # IPs, MACs, and type visible by default
        details_action.toggled.connect(self.canvas.set_show_details)
        view_menu.addAction(details_action)

        plan_menu = bar.addMenu("&Plan")
        plan_menu.addAction(self._action("&Auto layout", None, self._auto_layout))
        plan_menu.addAction(self._action("&Validate", None, self._validate))

    def _action(self, text: str, shortcut, slot) -> QAction:
        """Build a menu action whose slot is wrapped in _guarded.

        PyQt6 aborts the whole process on an unhandled exception inside
        a slot, so every menu action goes through the guard: failures
        surface as an error dialog and the app keeps running.
        """
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(self._guarded(slot))
        return action

    def _guarded(self, slot):
        """Wrap a slot so exceptions become an error dialog, not a crash."""

        @functools.wraps(slot)
        def wrapper(*args, **kwargs):
            try:
                return slot()
            except Exception as exc:  # noqa: BLE001 - last-resort UI guard
                traceback.print_exc()
                QMessageBox.critical(
                    self,
                    "Error",
                    f"That action failed:\n\n{type(exc).__name__}: {exc}",
                )

        return wrapper

    # --------------------------------------------------------------- handlers
    def _new_plan(self) -> None:
        self.controller.new_plan()
        self.canvas.refresh()

    def _save(self) -> None:
        self.controller.save()
        self.statusBar().showMessage("Plan saved", 3000)

    def _delete(self) -> None:
        """Delete whatever is selected on the canvas (devices and/or links)."""
        self.canvas.delete_selection()

    def _undo(self) -> None:
        self.controller.undo()
        self.canvas.refresh()

    def _redo(self) -> None:
        self.controller.redo()
        self.canvas.refresh()

    def _auto_layout(self) -> None:
        self.controller.run_auto_layout()
        self.canvas.refresh()

    def _validate(self) -> None:
        issues = self.controller.validate_plan()
        if not issues:
            QMessageBox.information(self, "Validation", "No issues found.")
            return
        text = "\n".join(f"[{i.severity.value}] {i.message}" for i in issues)
        QMessageBox.warning(self, "Validation issues", text)

    def _export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "", "PDF files (*.pdf)")
        if path:
            self.controller.export_to_pdf(Path(path))
            self.statusBar().showMessage(f"Exported {path}", 3000)

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "", "PNG files (*.png)")
        if path:
            self.controller.export_to_png(Path(path))
            self.statusBar().showMessage(f"Exported {path}", 3000)
