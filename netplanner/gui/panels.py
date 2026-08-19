"""Dockable side panels (properties editor, etc.)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDockWidget, QLabel, QVBoxLayout, QWidget

from netplanner.app.controller import AppController


class PropertiesPanel(QDockWidget):
    """Placeholder properties panel; wire selection -> fields here."""

    def __init__(self, controller: AppController, parent=None):
        super().__init__("Properties", parent)
        self.controller = controller
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("Select a device to edit its properties."))
        layout.addStretch()
        self.setWidget(body)

    @staticmethod
    def preferred_area() -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.RightDockWidgetArea
