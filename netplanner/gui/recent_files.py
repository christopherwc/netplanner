"""Recently opened .netplan projects, for the File > Open Recent menu.

Persisted as an ordered list of absolute paths via QSettings, most
recent first, capped at MAX_RECENT entries. A path that no longer
exists on disk is dropped the next time the list is loaded — a moved
or deleted file should stop haunting the menu rather than raising an
import error every time it's clicked.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings

from .app_settings import default_settings, restrict_settings_file

MAX_RECENT = 5
_SETTINGS_KEY = "recent/projects"


def _read_raw(settings: QSettings) -> list[str]:
    """QSettings collapses a one-item Python list to a bare string on
    some platforms/formats, so a str result has to be re-wrapped rather
    than iterated character by character."""
    raw = settings.value(_SETTINGS_KEY, [])
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def load_recent_files(settings: QSettings | None = None) -> list[Path]:
    """Recently opened projects, most recent first.

    Entries for files that no longer exist are dropped and the pruned
    list is written back, so a moved or deleted project's menu entry
    does not linger forever.
    """
    store = settings if settings is not None else default_settings()
    paths = [Path(p) for p in _read_raw(store)]
    existing = [p for p in paths if p.is_file()]
    if len(existing) != len(paths):
        store.setValue(_SETTINGS_KEY, [str(p) for p in existing])
        restrict_settings_file(store)
    return existing


def add_recent_file(path: Path, settings: QSettings | None = None) -> list[Path]:
    """Record `path` as the most recently opened project.

    Moves it to the front if already present, rather than duplicating
    it partway down the list.
    """
    store = settings if settings is not None else default_settings()
    resolved = path.resolve()
    remaining = [p for p in load_recent_files(store) if p != resolved]
    updated = [resolved, *remaining][:MAX_RECENT]
    store.setValue(_SETTINGS_KEY, [str(p) for p in updated])
    restrict_settings_file(store)
    return updated
