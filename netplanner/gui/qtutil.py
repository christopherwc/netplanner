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

import weakref
from collections.abc import Callable
from typing import Any

from PyQt6.QtWidgets import QApplication


def weak_call(instance: object, method_name: str, *args: Any) -> Callable[..., Any]:
    """A callable that dispatches to getattr(instance, method_name)(*args)
    without holding a strong reference to `instance`.

    Connecting a Qt signal straight to a bound method, or to a lambda
    that closes over `instance`, makes the connection itself keep
    `instance` alive for as long as the connection exists. When the
    signal's owner is itself a Qt child of `instance` -- a button living
    inside a dock widget's own layout, say -- that connection completes
    a genuine Python reference cycle: instance -> child widget ->
    connection -> slot -> instance. CPython's cyclic garbage collector
    can run at exactly the wrong moment inside a PyQt6 C extension call
    and segfault; this is a documented, confirmed-recurring failure
    mode in this codebase (see MainWindow._weak_call, tests/conftest.py,
    and issue #23, which diagnosed and fixed the first instance of it).
    Resolving `instance` through a weakref on every call means the
    connection never keeps it alive, so the cycle never forms.

    Whatever arguments the signal itself supplies (a checkbox's
    `toggled` bool, say) are ignored in favor of `*args`, bound here at
    connect time -- matching how a directly-connected bound method
    behaves when Qt calls it with fewer arguments than the signal
    emits.
    """
    weak_instance = weakref.ref(instance)

    def call(*_signal_args: Any, **_signal_kwargs: Any) -> Any:
        obj = weak_instance()
        return None if obj is None else getattr(obj, method_name)(*args)

    call.__name__ = method_name
    return call


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
