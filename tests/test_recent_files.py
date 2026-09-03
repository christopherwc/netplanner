"""Tests for the recently-opened-projects list.

Every test drives QSettings through an explicit .ini file under
tmp_path, never the real per-user config — same reasoning as
test_theme.py and test_logging.py's log_dir parameter.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 not installed")

from PyQt6.QtCore import QSettings

from netplanner.gui.recent_files import MAX_RECENT, add_recent_file, load_recent_files


@pytest.fixture()
def settings(tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    yield store
    store.clear()


def _touch(tmp_path, name: str):
    path = tmp_path / name
    path.write_text("{}")
    return path


def test_nothing_saved_yields_an_empty_list(settings):
    assert load_recent_files(settings) == []


def test_adding_a_file_puts_it_first(settings, tmp_path):
    a = _touch(tmp_path, "a.netplan")
    add_recent_file(a, settings)
    assert load_recent_files(settings) == [a.resolve()]


def test_the_most_recently_added_file_is_first(settings, tmp_path):
    a = _touch(tmp_path, "a.netplan")
    b = _touch(tmp_path, "b.netplan")
    add_recent_file(a, settings)
    add_recent_file(b, settings)
    assert load_recent_files(settings) == [b.resolve(), a.resolve()]


def test_re_adding_an_existing_entry_moves_it_to_front_without_duplicating(settings, tmp_path):
    a = _touch(tmp_path, "a.netplan")
    b = _touch(tmp_path, "b.netplan")
    add_recent_file(a, settings)
    add_recent_file(b, settings)
    add_recent_file(a, settings)
    assert load_recent_files(settings) == [a.resolve(), b.resolve()]


def test_the_list_is_capped_at_max_recent(settings, tmp_path):
    paths = [_touch(tmp_path, f"p{i}.netplan") for i in range(MAX_RECENT + 3)]
    for path in paths:
        add_recent_file(path, settings)
    loaded = load_recent_files(settings)
    assert len(loaded) == MAX_RECENT
    # Most recent MAX_RECENT entries, most-recent-first.
    assert loaded == [p.resolve() for p in reversed(paths[-MAX_RECENT:])]


def test_a_single_saved_entry_survives_qsettings_list_collapsing(settings, tmp_path):
    """QSettings can hand a one-item stringlist back as a bare string
    instead of a one-element list on some platforms/formats; loading
    must not iterate that string character by character."""
    a = _touch(tmp_path, "a.netplan")
    add_recent_file(a, settings)
    settings.setValue("recent/projects", str(a.resolve()))  # force the collapsed shape
    assert load_recent_files(settings) == [a.resolve()]


def test_a_deleted_file_is_dropped_and_the_list_is_pruned_on_disk(settings, tmp_path):
    a = _touch(tmp_path, "a.netplan")
    b = _touch(tmp_path, "b.netplan")
    add_recent_file(a, settings)
    add_recent_file(b, settings)
    a.unlink()

    assert load_recent_files(settings) == [b.resolve()]
    # Pruning persists, so a second load doesn't need to re-discover it.
    assert load_recent_files(settings) == [b.resolve()]


def test_a_null_stored_value_is_treated_as_no_recent_files(settings):
    settings.setValue("recent/projects", None)
    assert load_recent_files(settings) == []


def test_omitting_settings_falls_back_to_the_default_store(tmp_path):
    """conftest's isolate_default_qsettings redirects the default store
    to a per-test tmp directory, so this is safe to exercise directly
    rather than only incidentally through MainWindow(controller)."""
    a = _touch(tmp_path, "a.netplan")
    add_recent_file(a)
    assert load_recent_files() == [a.resolve()]
