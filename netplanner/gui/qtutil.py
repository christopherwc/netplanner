"""Narrowing the Optionals Qt hands back.

PyQt6 types a great many accessors as returning `T | None` — menuBar(),
statusBar(), addMenu(), horizontalHeader() — because the C++ API can
return a null pointer in situations Python code never reaches. A
QMainWindow creates its menu bar on first access and never yields None;
addMenu() returns None only for a menu bar that does not exist.

Reading through those Optionals with `x.foo()` is what produced most of
this project's type errors. The two usual answers are both wrong here.
`assert x is not None` disappears under `python -O`, taking the check
with it and leaving an AttributeError in its place — which is exactly
why the linter refuses asserts outside tests. `# type: ignore` silences
the report without establishing anything.

So: check, and raise something that names what was missing.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication


def required[T](value: T | None, what: str) -> T:
    """Return `value`, or raise if Qt handed back None.

    For accessors that cannot be None in this application's use of them.
    A raise here means an assumption about Qt was wrong, which is worth
    a stack trace naming the accessor rather than an AttributeError
    three frames later.
    """
    if value is None:
        raise RuntimeError(f"Qt returned no {what}, which should not be possible here")
    return value


def running_application() -> QApplication:
    """The live QApplication, narrowed from QCoreApplication.instance().

    QApplication.instance() is inherited from QCoreApplication and typed
    accordingly, so callers get `QCoreApplication | None` back even
    though this process only ever constructs a QApplication. An isinstance
    check earns the narrower type instead of a cast that would silently
    lie if that ever stopped being true.
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise RuntimeError("No QApplication instance is running")
    return app
