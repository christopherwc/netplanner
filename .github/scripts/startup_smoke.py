"""Launch the real application entry point and quit once it is up.

This exists because of a failure mode this project has actually shipped
twice: a missing import in a GUI module that no unit test touched, green
suite, crash on launch. The smoke tests build widgets directly; this
runs `netplanner.main:main` exactly as the console script does, so the
argument parsing, logging setup, repository construction, and window
show all have to survive for real.

`main()` creates its own QApplication and blocks in `exec()`, so we wrap
`exec` to schedule a quit as soon as the event loop starts. Exit code 0
means the window came up; anything else is a real traceback.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import netplanner.main as entry

_real_exec = QApplication.exec

# PyQt6 exposes exec() as a static method, so the wrapper swallows the
# instance argument that Python binds when it is called as app.exec().
def _exec_then_quit(*_args: object) -> int:
    QTimer.singleShot(1500, QApplication.quit)
    return _real_exec()


QApplication.exec = _exec_then_quit  # type: ignore[method-assign]

raise SystemExit(entry.main())
