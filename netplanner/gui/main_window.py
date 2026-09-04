"""Main application window (PyQt6)."""

from __future__ import annotations

import functools
import logging
import weakref
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
)

from netplanner.app.controller import AppController

from .canvas import NetworkCanvas
from .palette import EquipmentPalette
from .panels import PropertiesPanel
from .qtutil import required, running_application
from .recent_files import add_recent_file, load_recent_files
from .theme import Theme, apply_theme, capture_system_defaults, load_saved_theme, save_theme
from .vlan_panel import VlanPanel

logger = logging.getLogger(__name__)

# The extension is a convention rather than a requirement — the loader
# reads any JSON in the right shape — so the filter offers it without
# forcing it.
PROJECT_FILTER = "NetPlanner projects (*.netplan);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, settings: QSettings | None = None):
        super().__init__()
        self.controller = controller
        # `settings` lets tests inject a QSettings backed by a temp file
        # instead of writing the theme choice into the real user config.
        self._settings = settings
        app = running_application()
        self._system_defaults = capture_system_defaults(app)
        self._theme = load_saved_theme(self._settings)
        apply_theme(app, self._theme, self._system_defaults)
        self._update_title()
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

        self.vlan_panel = VlanPanel(controller, self)
        self.vlan_panel.filter_changed.connect(self.canvas.set_vlan_filter)
        # Canvas edits (placing a device, editing VLANs, deleting) never go
        # through the menu handlers, so the legend subscribes to the scene
        # instead of relying on _refresh_all alone.
        self.canvas.plan_changed.connect(self.vlan_panel.refresh)
        self.addDockWidget(self.vlan_panel.preferred_area(), self.vlan_panel)

        self._build_menus()

    # ----------------------------------------------------------------- menus
    def _build_menus(self) -> None:
        bar = required(self.menuBar(), "menu bar")

        file_menu = required(bar.addMenu("&File"), "File menu")
        file_menu.addAction(self._action(
            "&New plan", QKeySequence.StandardKey.New, self._weak_call("_new_plan")
        ))
        file_menu.addAction(self._action("&Rename plan…", None, self._weak_call("_rename_plan")))
        file_menu.addAction(self._action(
            "&Save", QKeySequence.StandardKey.Save, self._weak_call("_save")
        ))
        file_menu.addSeparator()
        file_menu.addAction(self._action(
            "&Open project…", None, self._weak_call("_import_project")
        ))
        recent_menu = required(file_menu.addMenu("Open &Recent"), "Open Recent menu")
        recent_menu.aboutToShow.connect(self._weak_call("_populate_recent_menu"))
        self._recent_menu = recent_menu
        file_menu.addAction(self._action(
            "E&xport project…", None, self._weak_call("_export_project")
        ))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Export &PDF…", None, self._weak_call("_export_pdf")))
        file_menu.addAction(self._action("Export P&NG…", None, self._weak_call("_export_png")))
        file_menu.addSeparator()
        file_menu.addAction(self._action(
            "&Quit", QKeySequence.StandardKey.Quit, self._weak_call("close")
        ))

        edit_menu = required(bar.addMenu("&Edit"), "Edit menu")
        edit_menu.addAction(self._action(
            "&Undo", QKeySequence.StandardKey.Undo, self._weak_call("_undo")
        ))
        edit_menu.addAction(self._action(
            "&Redo", QKeySequence.StandardKey.Redo, self._weak_call("_redo")
        ))
        edit_menu.addSeparator()
        edit_menu.addAction(
            self._action(
                "&Delete selected", QKeySequence.StandardKey.Delete, self._weak_call("_delete")
            )
        )

        view_menu = required(bar.addMenu("&View"), "View menu")
        details_action = QAction("Show device &details", self)
        details_action.setCheckable(True)
        details_action.setChecked(True)  # IPs, MACs, and type visible by default
        details_action.toggled.connect(self.canvas.set_show_details)
        view_menu.addAction(details_action)
        view_menu.addSeparator()
        view_menu.addAction(
            self._action("Zoom &In", QKeySequence.StandardKey.ZoomIn, self.canvas.zoom_in)
        )
        view_menu.addAction(
            self._action("Zoom &Out", QKeySequence.StandardKey.ZoomOut, self.canvas.zoom_out)
        )
        view_menu.addAction(
            self._action("&Reset Zoom", QKeySequence("Ctrl+0"), self.canvas.reset_zoom)
        )
        self._build_theme_menu(view_menu)

        plan_menu = required(bar.addMenu("&Plan"), "Plan menu")
        plan_menu.addAction(self._action("&Auto layout", None, self._weak_call("_auto_layout")))
        plan_menu.addAction(self._action("&Validate", None, self._weak_call("_validate")))

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

    def _weak_call(self, method_name: str, *args):
        """A callable that dispatches to getattr(self, method_name)(*args)
        without holding a strong reference to self.

        Every menu action passes its handler through here (when the
        handler is a method of this window rather than, say, the
        canvas) rather than a plain bound method like self._new_plan.
        PyQt's connection bookkeeping keeps whatever is passed to
        connect() alive for as long as the connection exists, and this
        window holds its own QActions back, transitively, through its
        menu bar -- so a bound method (or a lambda closing over self)
        here would complete that into a genuine Python-level reference
        cycle. That is the documented, confirmed-recurring cause of an
        intermittent segfault inside _build_menus, when CPython's
        cyclic garbage collector runs at exactly the wrong moment
        inside a C extension call (see tests/conftest.py and issue
        #23). Resolving self through a weakref each time this runs
        means the connection never keeps the window alive, so the
        cycle this bug depends on never forms.
        """
        weak_self = weakref.ref(self)

        def call(*_args, **_kwargs):
            window = weak_self()
            return None if window is None else getattr(window, method_name)(*args)

        call.__name__ = method_name
        return call

    def _guarded(self, slot):
        """Wrap a slot so exceptions become an error dialog, not a crash.

        Holds `self` only through a weakref, for the same reason
        _weak_call does: this wrapper is itself kept alive by a
        QAction's connection, and a direct closure over self here would
        be its own reference cycle regardless of what `slot` is.
        """
        weak_self = weakref.ref(self)

        @functools.wraps(slot)
        def wrapper(*args, **kwargs):
            try:
                return slot()
            except Exception as exc:
                # logger.exception records the full traceback in the log
                # file, so the dialog can stay short while the log holds
                # everything needed to diagnose the failure.
                logger.exception("Menu action '%s' failed", slot.__name__)
                from netplanner.log import log_file_path

                QMessageBox.critical(
                    weak_self(),  # None is a valid (parentless) QWidget parent
                    "Error",
                    f"That action failed:\n\n{type(exc).__name__}: {exc}\n\n"
                    f"Details were written to:\n{log_file_path()}",
                )

        return wrapper

    def _build_theme_menu(self, view_menu: QMenu) -> None:
        """View → Theme: System / Light / Dark, mutually exclusive."""
        theme_menu = required(view_menu.addMenu("&Theme"), "Theme menu")
        group = QActionGroup(self)
        group.setExclusive(True)
        self._theme_actions: dict[Theme, QAction] = {}
        for theme, label in (
            (Theme.SYSTEM, "&System"),
            (Theme.LIGHT, "&Light"),
            (Theme.DARK, "&Dark"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(theme is self._theme)
            action.triggered.connect(self._guarded(self._weak_call("_set_theme", theme)))
            group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[theme] = action

    def _set_theme(self, theme: Theme) -> None:
        app = running_application()
        apply_theme(app, theme, self._system_defaults)
        save_theme(theme, self._settings)
        self._theme = theme

    def _populate_recent_menu(self) -> None:
        """Rebuild Open Recent right before it's shown, from the current
        saved list — connected to aboutToShow instead of maintained
        incrementally, so it never goes stale between menu openings."""
        self._recent_menu.clear()
        paths = load_recent_files(self._settings)
        if not paths:
            empty = QAction("(No recent projects)", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for path in paths:
            action = QAction(str(path), self)
            action.triggered.connect(self._guarded(self._weak_call("_open_project_path", path)))
            self._recent_menu.addAction(action)

    # --------------------------------------------------------------- handlers
    def _new_plan(self) -> None:
        self.controller.new_plan()
        self._refresh_all()

    def _save(self) -> None:
        self.controller.save()
        required(self.statusBar(), "status bar").showMessage("Plan saved", 3000)

    def _delete(self) -> None:
        """Delete whatever is selected on the canvas (devices and/or links)."""
        self.canvas.delete_selection()

    def _update_title(self) -> None:
        """Window title tracks the plan name, so the current plan is
        identifiable without opening a dialog."""
        self.setWindowTitle(f"NetPlanner — {self.controller.plan.name}")

    def _rename_plan(self) -> None:
        """Rename the plan: this is the title on every export."""
        name, ok = QInputDialog.getText(
            self,
            "Rename plan",
            "Plan name (shown as the title on exports):",
            text=self.controller.plan.name,
        )
        if ok:
            self.controller.rename_plan(name)
            self._refresh_all()

    def _refresh_all(self) -> None:
        """Redraw the canvas and rebuild the VLAN legend after a change."""
        self.canvas.refresh()
        self.vlan_panel.refresh()
        self._update_title()

    def _undo(self) -> None:
        self.controller.undo()
        self._refresh_all()

    def _redo(self) -> None:
        self.controller.redo()
        self._refresh_all()

    def _auto_layout(self) -> None:
        self.controller.run_auto_layout()
        self._refresh_all()

    def _validate(self) -> None:
        issues = self.controller.validate_plan()
        if not issues:
            QMessageBox.information(self, "Validation", "No issues found.")
            return
        text = "\n".join(f"[{i.severity.value}] {i.message}" for i in issues)
        QMessageBox.warning(self, "Validation issues", text)

    def _import_project(self) -> None:
        """Open a .netplan file, replacing the current plan."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", PROJECT_FILTER
        )
        if not path:
            return
        self._open_project_path(Path(path))

    def _open_project_path(self, path: Path) -> None:
        """Import `path`, replacing the current plan, and remember it.

        Shared by the file dialog and by clicking an Open Recent entry,
        so both record the choice in the recent-projects list the same
        way.
        """
        self.controller.import_project(path)
        add_recent_file(path, self._settings)
        # Same refresh as _new_plan: the plan object was replaced, so
        # the canvas, panels and title are all describing something that
        # no longer exists.
        self._refresh_all()

    def _export_project(self) -> None:
        """Write a .netplan file, after warning about attached configs."""
        carriers = self.controller.plan.devices_carrying_configs()
        if carriers and not self._confirm_config_disclosure(carriers):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export project", "", PROJECT_FILTER
        )
        if path:
            self.controller.export_project(Path(path))

    def _confirm_config_disclosure(self, carriers: list[str]) -> bool:
        """Ask before writing attached configs into a shareable file.

        Asked before the file dialog rather than after, so answering no
        costs nothing. The device names are listed because "this plan
        has configs" is easy to wave through, while seeing core-sw named
        is what makes someone remember what is in it.
        """
        shown = ", ".join(carriers[:5])
        if len(carriers) > 5:
            shown += f", and {len(carriers) - 5} more"
        answer = QMessageBox.warning(
            self,
            "Attached configs travel with this file",
            f"{len(carriers)} device(s) carry an attached config: {shown}.\n\n"
            "Exported project files contain those configs verbatim, "
            "including any enable secrets, SNMP community strings or "
            "pre-shared keys in them.\n\n"
            "Only send this file somewhere you would send the configs "
            "themselves.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,  # the safe answer is the default
        )
        return answer == QMessageBox.StandardButton.Ok

    def _export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "", "PDF files (*.pdf)")
        if path:
            self.controller.export_to_pdf(Path(path))
            required(self.statusBar(), "status bar").showMessage(f"Exported {path}", 3000)

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "", "PNG files (*.png)")
        if path:
            self.controller.export_to_png(Path(path))
            required(self.statusBar(), "status bar").showMessage(f"Exported {path}", 3000)
