"""Light / dark / system color theme.

Three states, all reversible: System leaves the app exactly as Qt and
the desktop theme engine hand it over — the only appearance NetPlanner
had before this module existed. Light and Dark both force the Fusion
style, because native styles on several platforms silently ignore
QPalette overrides for some widgets; Fusion is the one style that
reliably honours every role set below.

Switching back to System has to restore what was there before, and
there is no Qt API to ask for that after the fact — once a palette is
overwritten, the previous one is gone. So it is captured once, by
`capture_system_defaults`, before anything in this module ever touches
the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .qtutil import required

_SETTINGS_KEY = "ui/theme"


class Theme(Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class SystemDefaults:
    """The style and palette in effect before any theme was applied."""

    style_name: str
    palette: QPalette


def capture_system_defaults(app: QApplication) -> SystemDefaults:
    """Snapshot the app's current style/palette so System can restore it.

    Must be called before `apply_theme` ever runs on `app` — otherwise
    what gets captured is a previously forced theme, not the original.
    """
    return SystemDefaults(
        style_name=required(app.style(), "application style").objectName(),
        palette=QPalette(app.palette()),
    )


def _light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 233, 233))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(0, 0, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(48, 140, 198))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return palette


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(90, 160, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    # Disabled controls get their own, dimmer set — without this, disabled
    # text stays full-brightness white on the same dark background as
    # enabled text and the two become indistinguishable.
    dim = QColor(127, 127, 127)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, dim)
    return palette


def apply_theme(app: QApplication, theme: Theme, defaults: SystemDefaults) -> None:
    """Set the app's style and palette for `theme`."""
    if theme is Theme.DARK:
        app.setStyle("Fusion")
        app.setPalette(_dark_palette())
    elif theme is Theme.LIGHT:
        app.setStyle("Fusion")
        app.setPalette(_light_palette())
    else:
        app.setStyle(defaults.style_name)
        app.setPalette(defaults.palette)


def _default_settings() -> QSettings:
    return QSettings("NetPlanner", "NetPlanner")


def load_saved_theme(settings: QSettings | None = None) -> Theme:
    """The theme saved from a previous run, defaulting to System.

    A value that is not one of the three known strings — a settings
    file from a future version, or hand-edited — falls back to System
    rather than raising during startup.
    """
    store = settings if settings is not None else _default_settings()
    raw = store.value(_SETTINGS_KEY, Theme.SYSTEM.value)
    try:
        return Theme(raw)
    except (ValueError, TypeError):
        return Theme.SYSTEM


def save_theme(theme: Theme, settings: QSettings | None = None) -> None:
    store = settings if settings is not None else _default_settings()
    store.setValue(_SETTINGS_KEY, theme.value)
