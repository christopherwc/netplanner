"""Main application window (PyQt6)."""

from __future__ import annotations

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

        plan_menu = bar.addMenu("&Plan")
        plan_menu.addAction(self._action("&Auto layout", None, self._auto_layout))
        plan_menu.addAction(self._action("&Validate", None, self._validate))

    def _action(self, text: str, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        return action

    # --------------------------------------------------------------- handlers
    def _new_plan(self) -> None:
        self.controller.new_plan()
        self.canvas.refresh()

    def _save(self) -> None:
        self.controller.save()
        self.statusBar().showMessage("Plan saved", 3000)

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
