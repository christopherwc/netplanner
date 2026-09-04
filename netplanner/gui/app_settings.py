"""The QSettings store NetPlanner's GUI preferences are persisted to.

Shared by theme.py and recent_files.py so both fall back to the same
physical file rather than each resolving their own.

NETPLANNER_SETTINGS_PATH overrides the OS-default location (the same
role NETPLANNER_LOG_DIR plays for log.py). It exists for test
isolation: QSettings("NetPlanner", "NetPlanner") resolves and caches
its storage path the first time any such object is constructed
anywhere in the process, and QSettings.setPath() calls after that
point are silently ignored for that (format, scope) pair — so
redirecting per test by calling setPath() in a fixture does not work
past the first test that touches the default store. Pointing every
default QSettings at an explicit file via this env var sidesteps that
cache entirely, since Qt keys explicit-path QSettings by their own
path rather than by (format, scope, organization, application).
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QSettings

from netplanner.permissions import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, restrict_to_owner

_ENV_OVERRIDE = "NETPLANNER_SETTINGS_PATH"


def default_settings() -> QSettings:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings("NetPlanner", "NetPlanner")


def restrict_settings_file(settings: QSettings) -> None:
    """Narrow the settings file and its directory to owner-only.

    Unlike the plans database and the log directory, QSettings writes
    the preferences file (theme choice, the recent-projects list) under
    the ordinary process umask — typically 0644/0755, readable by every
    other account on the machine. The recent-projects list is absolute
    paths to .netplan files, which can leak a client or project name
    through its directory layout the same way an unstripped config path
    in an export could — see the path-stripping in project_file.py.

    Called after every write rather than once at startup: sync() can
    recreate the file, which would otherwise reset its mode back to the
    umask default.
    """
    settings.sync()  # flush to disk first — chmod needs the file to exist
    path = Path(settings.fileName())
    restrict_to_owner(path, PRIVATE_FILE_MODE)
    restrict_to_owner(path.parent, PRIVATE_DIR_MODE)
