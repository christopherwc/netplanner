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

from PyQt6.QtCore import QSettings

_ENV_OVERRIDE = "NETPLANNER_SETTINGS_PATH"


def default_settings() -> QSettings:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings("NetPlanner", "NetPlanner")
