"""The paths that were excluded from the coverage number, not covered by it.

A `# pragma: no cover` is a claim that a line cannot run. The claim is
usually about Qt — "Qt always supplies a painter", "the validator blocks
these" — and it is usually true of the application. It is not true of the
code, which any caller can reach directly, and an untested defensive
branch is exactly the one that misbehaves the day the assumption breaks.

These tests call those paths on purpose so the pragmas can come off and
the coverage figure can mean what it says.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from unittest.mock import MagicMock

from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import QApplication, QLabel

from netplanner.app.controller import AppController
from netplanner.domain.entities import DeviceType, LinkType
from netplanner.gui.canvas import DeviceItem, LinkItem, SiteItem, TextBoxItem
from netplanner.gui.dialogs import (
    COL_MAX_SPEED,
    COL_NAME,
    COL_UNIT,
    DevicePropertiesDialog,
    _SpeedEdit,
    _UnitCombo,
)


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def controller():
    return AppController(repository=MagicMock())


@pytest.fixture()
def tab(app, controller):
    """The interfaces tab of a real device dialog, four rows deep."""
    device = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    dialog = DevicePropertiesDialog(device)
    yield dialog._interfaces
    dialog.deleteLater()


# --------------------------------------------------------------- null painter
def test_every_item_declines_to_paint_without_a_painter(app, controller):
    """Qt types the painter as a pointer, so every paint() signature
    admits None. Nothing in the application passes one, but the
    signature says a caller may, and the guard has to hold when it does.
    """
    device = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    other = controller.add_device("rtr1", DeviceType.ROUTER, 300, 0)
    link = controller.add_link(device.id, other.id, LinkType.FIBER)
    site = controller.add_site("Rack 3", 0, 0)
    textbox = controller.add_textbox("DMZ", 400, 400)

    items = [
        DeviceItem(device, controller),
        SiteItem(site, controller),
        LinkItem(link, controller, 0, 0, 300, 0, QPen()),
        TextBoxItem(textbox, controller),
    ]
    for item in items:
        item.paint(None, None)  # returns rather than raising


def test_the_highlighter_declines_a_null_line(app):
    """QSyntaxHighlighter.highlightBlock is typed to accept None for the
    same reason, and the subclass has to match its base signature."""
    from netplanner.domain.entities import ConfigFormat
    from netplanner.gui.config_viewer import ConfigHighlighter

    highlighter = ConfigHighlighter(None, ConfigFormat.CISCO_IOS)
    highlighter.highlightBlock(None)  # returns rather than raising


# ------------------------------------------------------- the speed field
def test_text_that_is_not_a_number_reads_as_unknown(app):
    """The validator only filters typing. setText, a paste handled in
    code, or a programmatic caller all reach the field directly, and
    float() raises on what arrives."""
    edit = _SpeedEdit(1_000, 1_000)

    edit.setText("not a number")

    assert edit.mbps(1_000) is None
    edit.deleteLater()


def test_a_speed_rounding_below_a_megabit_reads_as_unknown(app):
    """A figure too small to be a rate is not a rate."""
    edit = _SpeedEdit(1_000, 1_000)
    edit.setText("0.0000001")

    assert edit.mbps(1_000) is None
    edit.deleteLater()


# ------------------------------------------------- rows that are not there
def test_a_widget_that_belongs_to_no_row_reports_none(tab):
    """_row_of scans the table for the widget that raised a signal. A
    widget whose row was removed is not in it, and -1 is how the
    handlers below learn to do nothing."""
    orphan = _UnitCombo()

    assert tab._row_of(orphan, COL_UNIT) == -1
    orphan.deleteLater()


def test_a_unit_change_from_a_removed_row_does_nothing(tab):
    """Qt can deliver a queued signal after the row it came from is
    gone. The handler resolves the row at signal time, gets -1, and
    must return rather than index the table with it."""
    orphan = _UnitCombo()

    tab._on_unit_changed(orphan)  # row resolves to -1

    orphan.deleteLater()


def test_refreshing_a_row_that_is_not_there_does_nothing(tab):
    tab._refresh_negotiated(-1)  # returns rather than indexing


def test_a_row_whose_speed_cell_is_not_a_speed_field_has_no_rate(tab):
    """The column is filled by one function, so this cannot happen
    through the UI — but _row_rate_mbps is reached from three handlers
    and reads whatever the cell holds."""
    tab.table.setCellWidget(0, COL_MAX_SPEED, QLabel("not a speed field"))

    assert tab._row_rate_mbps(0) is None


def test_a_unit_change_over_a_foreign_speed_cell_leaves_it_alone(tab):
    """Same cell, reached through the unit handler: it must skip the
    re-expression rather than call mbps() on a QLabel."""
    tab.table.setCellWidget(0, COL_MAX_SPEED, QLabel("not a speed field"))
    unit_combo = tab.table.cellWidget(0, COL_UNIT)

    tab._on_unit_changed(unit_combo)

    assert isinstance(tab.table.cellWidget(0, COL_MAX_SPEED), QLabel)


def test_a_row_with_no_name_cell_is_skipped_when_harvesting(tab):
    """Every row gets a name item when it is built, so a row without
    one only happens if something took it away. Harvesting must skip
    the row rather than read a name off None."""
    tab.table.takeItem(0, COL_NAME)

    names = [i.name for i in tab.result_interfaces()]

    assert len(names) == tab.table.rowCount() - 1


def test_a_row_with_no_name_cell_negotiates_nothing(tab):
    """The same missing item reached through the redraw path."""
    tab.table.takeItem(0, COL_NAME)

    tab._refresh_negotiated(0)  # returns rather than reading the id off None


# --------------------------------------------------------- word wrap tails
def test_a_blank_line_in_site_notes_adds_nothing(app, controller):
    """_wrap_site_notes splits on newlines and does not skip the empty
    paragraph a blank line produces, so its word loop never runs and the
    accumulator is still empty at the tail. The two other wrap helpers
    guard against this before the loop; this one does not."""
    from netplanner.export.renderer import _wrap_site_notes

    site = controller.add_site("Rack 3", 0, 0)
    site.notes = "first line\n\nthird line"

    assert _wrap_site_notes(site) == ["first line", "third line"]


def test_the_gutter_skips_blocks_outside_the_repaint_region(app):
    """Qt repaints only the damaged rectangle. A block above it still
    comes back from firstVisibleBlock() and must be stepped over rather
    than numbered, or the line numbers drift against the text."""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPaintEvent

    from netplanner.domain.entities import ConfigFile, ConfigFormat
    from netplanner.gui.config_viewer import ConfigTextView

    config = ConfigFile(
        filename="run.cfg",
        content="\n".join(f"line {n}" for n in range(40)),
        config_format=ConfigFormat.CISCO_IOS,
    )
    view = ConfigTextView(config)
    view.resize(400, 300)

    # A damage rect that starts well below the first block: the early
    # blocks are visible but outside it.
    view.paint_gutter(QPaintEvent(QRect(0, 200, 400, 100)))

    view.deleteLater()
