"""Tests for the shared QSettings factory.

conftest's isolate_default_qsettings fixture sets NETPLANNER_SETTINGS_PATH
for every test, so covering the no-override branch means deliberately
unsetting it here — read-only, so nothing actually touches the real
~/.config/NetPlanner even though this constructs the OS-default store.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")


def test_default_settings_falls_back_to_the_os_default_store(monkeypatch):
    monkeypatch.delenv("NETPLANNER_SETTINGS_PATH", raising=False)
    from netplanner.gui.app_settings import default_settings

    settings = default_settings()
    assert settings.organizationName() == "NetPlanner"
    assert settings.applicationName() == "NetPlanner"


def test_default_settings_honours_the_override(tmp_path, monkeypatch):
    from netplanner.gui.app_settings import default_settings

    override = tmp_path / "custom.ini"
    monkeypatch.setenv("NETPLANNER_SETTINGS_PATH", str(override))

    settings = default_settings()
    settings.setValue("probe", "1")
    settings.sync()

    assert override.exists()
