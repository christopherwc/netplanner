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

A third, related failure showed up later, intermittently, on CI only
(never reproduced locally, including under a 2000-iteration stress
loop — see disable_automatic_gc below): the same "Fatal Python error:
Segmentation fault" signature, inside the same _build_menus, at a
different line each time despite running the identical test. That
inconsistency is the signature of a different bug than the one above,
not a recurrence of it — a Qt object's C++ side torn down while its
Python wrapper is still reachable, corrupting memory that a later,
unrelated allocation then trips over wherever it happens to land.

Every QAction connected here (`_action`, `_build_theme_menu`,
`_populate_recent_menu`) closes over `self` — a bound method or a
lambda capturing the MainWindow — and PyQt's connection bookkeeping
holds that closure alive. MainWindow, transitively through its menu
bar, holds the QAction. That is a reference cycle, and CPython's
automatic cyclic collector runs whenever an allocation trips its
generation-0 threshold, including from inside a C extension call —
so it can run in the middle of a *different* MainWindow's
`_build_menus`, on a *different* test's objects, at a point Qt never
promised was reentrant. This is a documented category of PyQt/PySide
bug (Qt's own tracker: PYSIDE-1919; multiple incidents in pyqtgraph),
not something specific to this file.

disable_automatic_gc and dispose_widgets below (PR #16) are a
mitigation, not a fix: they reduce the odds of the collector running at
an unsafe moment, but the crash recurred repeatedly afterward, on
Python 3.13 and 3.14, in the "Tests" and "Container image" CI jobs
alike — never on 3.12, and never reproduced locally. Since it is a
non-deterministic native crash rather than a real assertion failure,
.github/workflows/ci.yml retries the pytest invocation once, but only
on exit 139 (SIGSEGV) specifically, so an actual test failure is never
silently masked.
"""

from __future__ import annotations

import gc

import pytest

from netplanner.persistence.repository import PlanRepository

try:  # the domain and persistence suites run without PyQt6 installed
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt6 is a hard dependency in CI
    QApplication = None


@pytest.fixture(autouse=True, scope="session")
def disable_automatic_gc():
    """Take Python's cyclic GC off its own automatic schedule.

    The standard workaround for the class of bug described in the
    module docstring: disable it here, and dispose_widgets below calls
    gc.collect() explicitly once a test's Qt objects are already torn
    down at the C++ level (close() + deleteLater() + a drained
    DeferredDelete queue), which is the one point in the cycle this
    suite can guarantee no Qt call is in flight. That replaces an
    unpredictable collection with a deterministic one, at a moment
    known to be safe rather than whichever moment an allocation
    threshold happens to be crossed.
    """
    if QApplication is None:
        yield
        return
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture(autouse=True)
def isolate_default_qsettings(tmp_path, monkeypatch):
    """Redirect the app's default QSettings store off the real
    ~/.config/NetPlanner, to a fresh file for every test.

    MainWindow falls back to that default store whenever a caller
    doesn't inject one — its `settings` constructor param defaults to
    None — and several tests build a MainWindow that way and then
    exercise a path that writes through it (importing a project
    records it in the recent-files list).

    Setting NETPLANNER_SETTINGS_PATH (see app_settings.py), not
    QSettings.setPath(): Qt resolves and caches the default store's
    path the first time any QSettings("NetPlanner", "NetPlanner") is
    constructed anywhere in the process, and ignores setPath() calls
    after that — so redirecting that way only ever affects whichever
    test happens to run first, not each test in turn. An explicit path
    read fresh on every call sidesteps that cache.
    """
    if QApplication is None:
        yield
        return
    monkeypatch.setenv("NETPLANNER_SETTINGS_PATH", str(tmp_path / "netplanner-settings.ini"))
    yield


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

    The gc.collect() at the end is new, and pairs with
    disable_automatic_gc above: by the time it runs, every Qt object
    from this test is already destroyed at the C++ level, so collecting
    now breaks any Python-side reference cycles (a QAction's connection
    closing over `self`, see the module docstring) right away, at a
    point already established to be safe — rather than leaving Python
    to collect them automatically, at a point that isn't.
    """
    yield
    application = QApplication.instance() if QApplication is not None else None
    if application is None:
        return  # a test that never touched Qt
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


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
