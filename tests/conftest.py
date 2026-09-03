"""Shared fixtures.

Two autouse fixtures, both cleaning up handles the tests open and never
close. The first closes database handles. A PlanRepository
owns a SQLAlchemy engine whose pool keeps the SQLite file open until
dispose() is called; tests create one per test and mostly do not close
it. On Python 3.13+ the interpreter reports each of those as
"ResourceWarning: unclosed database" when the connection is finalized
during garbage collection, and because the suite runs with warnings as
errors, those surface as failures attached to whichever unlucky test
happened to trigger the collection — never the test that opened the
handle. This makes the cleanup deterministic instead.

Production code closes its own repository (AppController.close, called
from main); this is only for tests, which open a great many of them.

The second disposes Qt widgets. A test that builds a MainWindow builds a
canvas, a scene, a palette and two docks with it, and none of that is
freed when the test function returns: Python drops its reference, but
Qt's C++ object outlives it, parented to nothing and owned by the
application. deleteLater() does not help on its own either — it queues a
deletion that only runs when the event loop next turns, and a test suite
has no event loop.

So the widgets accumulate for the whole session. Fourteen MainWindows
and their object graphs were alive by the end of a full run, which is
what a segmentation fault inside QMenuBar.addMenu turned out to be
downstream of: not a bug in the menu, a heap the suite had spent several
hundred tests filling.
"""

from __future__ import annotations

import pytest

from netplanner.persistence.repository import PlanRepository

try:  # the domain and persistence suites run without PyQt6 installed
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt6 is a hard dependency in CI
    QApplication = None


@pytest.fixture(autouse=True)
def close_repositories(monkeypatch):
    """Dispose every engine opened during a test, in reverse order."""
    opened: list[PlanRepository] = []
    original_init = PlanRepository.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(PlanRepository, "__init__", tracking_init)
    yield
    for repository in reversed(opened):
        repository.close()


@pytest.fixture(autouse=True)
def dispose_widgets():
    """Close and delete every top-level widget a test leaves behind.

    sendPostedEvents with DeferredDelete is the part that matters, and
    processEvents() is not a substitute: it deliberately skips
    DeferredDelete, so a queue of deleteLater() calls survives it intact.
    Draining that queue by hand is the only thing that frees anything
    here, because a test suite never turns an event loop.

    Getting this wrong is worse than doing nothing. deleteLater() hands
    ownership to Qt, which stops PyQt's refcounting from freeing the
    widget when Python drops it — so a fixture that queues deletions and
    never drains them leaks harder than no fixture at all. Measured:
    30 widgets surviving with no fixture, 61 with one that called
    processEvents(), 0 with this one.
    """
    yield
    application = QApplication.instance() if QApplication is not None else None
    if application is None:
        return  # a test that never touched Qt
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def restore_app_theme():
    """Undo any QApplication style/palette change a test makes.

    MainWindow applies the saved theme by calling QApplication.setStyle
    and setPalette, which — unlike per-widget state — is process-global
    and outlives the test that triggered it. Without this, one test
    switching to dark mode would leak into every MainWindow built
    afterward, including in unrelated test modules that share the same
    QApplication instance (see the module docstring above).
    """
    application = QApplication.instance() if QApplication is not None else None
    if application is None:
        yield
        return
    style_name = application.style().objectName()
    palette = QPalette(application.palette())
    yield
    application.setStyle(style_name)
    application.setPalette(palette)
