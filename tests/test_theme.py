"""Tests for the light/dark/system theme.

Every test drives QSettings through an explicit .ini file under
tmp_path, never the real per-user config Qt would otherwise write to
under $HOME/.config — the same reason setup_logging() takes a log_dir
in test_logging.py.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from netplanner.gui.theme import (
    SystemDefaults,
    Theme,
    apply_theme,
    capture_system_defaults,
    load_saved_theme,
    save_theme,
)


@pytest.fixture(scope="module")
def app():
    """One QApplication for the module; Qt forbids more than one."""
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    yield store
    store.clear()


# ------------------------------------------------------------- persistence
def test_default_theme_is_system_when_nothing_saved(settings):
    assert load_saved_theme(settings) is Theme.SYSTEM


def test_saved_theme_round_trips(settings):
    save_theme(Theme.DARK, settings)
    assert load_saved_theme(settings) is Theme.DARK

    save_theme(Theme.LIGHT, settings)
    assert load_saved_theme(settings) is Theme.LIGHT


def test_a_settings_file_with_an_unknown_value_falls_back_to_system(settings):
    """A future version's theme name, or a hand-edited file — anything
    that is not one of the three known strings must not crash startup."""
    settings.setValue("ui/theme", "solarized")
    assert load_saved_theme(settings) is Theme.SYSTEM


def test_a_non_string_stored_value_falls_back_to_system(settings):
    settings.setValue("ui/theme", 12345)
    assert load_saved_theme(settings) is Theme.SYSTEM


# ----------------------------------------------------------------- palette
def test_capture_system_defaults_snapshots_current_style_and_palette(app):
    defaults = capture_system_defaults(app)
    assert isinstance(defaults, SystemDefaults)
    assert defaults.style_name == app.style().objectName()


def test_light_and_dark_palettes_differ(app):
    """restore_app_theme (conftest) resets the app's palette afterward."""
    defaults = capture_system_defaults(app)
    apply_theme(app, Theme.LIGHT, defaults)
    light_base = app.palette().base().color()
    apply_theme(app, Theme.DARK, defaults)
    dark_base = app.palette().base().color()
    assert light_base != dark_base
    # Dark mode's Base color must actually be dark, not a token swap.
    assert dark_base.lightness() < light_base.lightness()


def test_dark_palette_defines_a_dimmer_disabled_text_color(app):
    from PyQt6.QtGui import QPalette

    from netplanner.gui.theme import _dark_palette

    palette = _dark_palette()
    enabled = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    assert disabled != enabled


def test_system_restores_the_captured_defaults(app):
    """Round-tripping through Light and Dark must land back on exactly
    what was there before either was ever applied."""
    defaults = capture_system_defaults(app)
    original_style = app.style().objectName()
    original_base = app.palette().base().color()
    apply_theme(app, Theme.DARK, defaults)
    apply_theme(app, Theme.LIGHT, defaults)
    apply_theme(app, Theme.SYSTEM, defaults)
    assert app.style().objectName() == original_style
    assert app.palette().base().color() == original_base
