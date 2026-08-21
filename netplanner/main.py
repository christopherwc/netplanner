"""Application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    """Start the GUI, with logging configured before anything else runs."""
    import logging
    import platform

    from PyQt6.QtWidgets import QApplication

    from netplanner.app.controller import AppController
    from netplanner.gui.main_window import MainWindow
    from netplanner.log import log_file_path, setup_logging

    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info(
        "NetPlanner starting (python %s on %s); log file: %s",
        platform.python_version(), platform.system(), log_file_path(),
    )

    app = QApplication(sys.argv)
    controller = AppController()
    window = MainWindow(controller)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
