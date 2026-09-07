"""Dockable VLAN legend and filter.

Lists every VLAN in use across the plan with its color swatch, name,
and a summary of where it appears. Ticking VLANs highlights their
members on the canvas and dims everything else; with nothing ticked the
diagram renders normally.

The legend is rebuilt from the plan on demand rather than kept in sync
incrementally — VLAN membership is derived from interfaces, so any edit
can change it, and recomputing is cheap next to the redraw it triggers.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from netplanner.app.controller import AppController
from netplanner.export.vlans import VlanUsage, plan_vlan_usage
from netplanner.gui.qtutil import required, weak_call

SWATCH = 12  # px square color chip in the legend


class VlanPanel(QDockWidget):
    """VLAN legend with per-VLAN highlight checkboxes."""

    filter_changed = pyqtSignal(object)  # set[int] of selected VLAN ids

    def __init__(self, controller: AppController, parent=None):
        super().__init__("VLANs", parent)
        self.controller = controller
        self._checkboxes: dict[int, QCheckBox] = {}

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(8, 8, 8, 8)

        intro = QLabel(
            "Tick a VLAN to highlight the devices and interfaces carrying it. "
            "Untick everything to show the plan normally."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #666; font-size: 11px;")
        self._layout.addWidget(intro)

        buttons = QHBoxLayout()
        all_btn = QPushButton("Select all")
        # weak_call, not a lambda closing over self: these buttons are
        # Qt children of this dock widget (via container -> setWidget),
        # so a closure capturing self would complete a reference cycle
        # back to it -- the same bug class fixed in MainWindow (see
        # qtutil.weak_call and issue #23), and this dock lives for the
        # app's whole lifetime just like MainWindow does.
        all_btn.clicked.connect(weak_call(self, "_set_all", True))
        none_btn = QPushButton("Clear")
        none_btn.clicked.connect(weak_call(self, "_set_all", False))
        buttons.addWidget(all_btn)
        buttons.addWidget(none_btn)
        buttons.addStretch()
        self._layout.addLayout(buttons)

        # The VLAN rows live in their own scrollable widget so a plan with
        # many VLANs doesn't push the buttons off the dock.
        self._rows_host = QWidget()
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_host)
        self._layout.addWidget(scroll)

        self._empty_label = QLabel("No VLANs in this plan yet.")
        self._empty_label.setStyleSheet("color: #888; font-style: italic;")
        self._layout.addWidget(self._empty_label)

        self.setWidget(container)
        self.refresh()

    # ---------------------------------------------------------------- build
    def refresh(self) -> None:
        """Rebuild the VLAN list from the current plan, keeping ticks.

        Selections survive a refresh so adding a device doesn't silently
        drop the user's highlight; VLANs that vanished are forgotten.
        """
        selected = self.selected_vlans()

        while self._rows.count():
            item = required(self._rows.takeAt(0), "layout item")
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checkboxes.clear()

        usages = plan_vlan_usage(self.controller.plan)
        self._empty_label.setVisible(not usages)

        for usage in usages:
            self._rows.addWidget(self._make_row(usage, usage.vlan_id in selected))
        self._rows.addStretch()

    def _make_row(self, usage: VlanUsage, checked: bool) -> QWidget:
        """One legend entry: swatch, checkbox label, and a usage summary."""
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(6)
        swatch = QLabel()
        pixmap = QPixmap(SWATCH, SWATCH)
        pixmap.fill(QColor(usage.color))
        swatch.setPixmap(pixmap)
        # Reserve the swatch's own width; without a fixed size the label
        # collapses to zero and the checkbox text draws over the chip.
        swatch.setFixedSize(SWATCH, SWATCH)
        top.addWidget(swatch, 0, Qt.AlignmentFlag.AlignVCenter)

        checkbox = QCheckBox(usage.label)
        checkbox.setChecked(checked)
        # weak_call: checkbox is a Qt child of this dock widget (via
        # row -> self._rows_host), same reasoning as the buttons above.
        checkbox.toggled.connect(weak_call(self, "_emit_filter"))
        self._checkboxes[usage.vlan_id] = checkbox
        top.addWidget(checkbox)
        top.addStretch()
        layout.addLayout(top)

        summary = QLabel(usage.summary)
        summary.setStyleSheet("color: #777; font-size: 10px; margin-left: 20px;")
        layout.addWidget(summary)
        return row

    # --------------------------------------------------------------- filter
    def selected_vlans(self) -> set[int]:
        """VLAN ids currently ticked."""
        return {vlan_id for vlan_id, box in self._checkboxes.items() if box.isChecked()}

    def _set_all(self, checked: bool) -> None:
        # Block signals so a bulk toggle emits one filter change, not one
        # per VLAN — otherwise the canvas repaints N times.
        for box in self._checkboxes.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._emit_filter()

    def _emit_filter(self) -> None:
        self.filter_changed.emit(self.selected_vlans())

    @staticmethod
    def preferred_area() -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.RightDockWidgetArea
