"""Application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from netplanner.app.controller import AppController
    from netplanner.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    controller = AppController()
    window = MainWindow(controller)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
