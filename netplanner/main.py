"""Application entry point."""

from __future__ import annotations

import logging
import sys
from types import TracebackType

logger = logging.getLogger(__name__)


def _report_unhandled(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    """Log an unhandled exception and tell the user where to look.

    Qt calls sys.excepthook for exceptions escaping a slot and then
    aborts the process, so without this the app vanishes with nothing
    written down. The abort still happens — Python cannot veto it — but
    the traceback reaches the log file and the user gets told why the
    window disappeared instead of watching it evaporate.

    Menu actions are already wrapped by MainWindow._guarded and recover
    in place; this is the net under everything else, chiefly canvas and
    dialog event handlers.
    """
    from PyQt6.QtWidgets import QMessageBox

    from netplanner.log import log_file_path

    logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
    QMessageBox.critical(
        None,
        "NetPlanner",
        f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc}\n\n"
        f"The full details were written to:\n{log_file_path()}",
    )


def main() -> int:
    """Start the GUI, with logging configured before anything else runs."""
    import platform

    from PyQt6.QtWidgets import QApplication, QMessageBox

    from netplanner.app.controller import AppController
    from netplanner.gui.main_window import MainWindow
    from netplanner.log import log_file_path, setup_logging

    setup_logging()
    logger.info(
        "NetPlanner starting (python %s on %s); log file: %s",
        platform.python_version(), platform.system(), log_file_path(),
    )

    app = QApplication(sys.argv)
    sys.excepthook = _report_unhandled

    # Startup touches the filesystem — the data directory, the database,
    # its schema — and any of it can fail on a read-only home or a
    # corrupt file. Before this, that surfaced as a traceback on a
    # stderr nobody reads when launching from a desktop menu, and no
    # window ever appeared.
    try:
        controller = AppController()
        window = MainWindow(controller)
    except Exception as exc:
        logger.exception("Startup failed")
        QMessageBox.critical(
            None,
            "NetPlanner could not start",
            f"{type(exc).__name__}: {exc}\n\n"
            f"The full details were written to:\n{log_file_path()}",
        )
        return 1

    window.show()
    try:
        return app.exec()
    finally:
        # The repository pools SQLite connections and holds the database
        # file open; closing it here means shutdown releases the handles
        # deterministically instead of leaving them to the collector.
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
