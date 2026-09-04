"""Central logging configuration for NetPlanner.

Philosophy
----------
Logging and error handling here follow three rules:

1. **Loggers are per-module.** Every module calls
   ``logging.getLogger(__name__)`` so log lines carry their origin
   (``netplanner.persistence.repository: ...``) and subsystems can be
   silenced or amplified individually.

2. **Exceptions are caught at boundaries, not blankets.** Pure logic
   (entities, geometry, card layout) raises freely; the places that
   talk to the outside world — SQLite, the filesystem, exporters, Qt
   slots — catch narrow exception types, log them with full tracebacks,
   and re-raise a NetPlanner error type whose message says *what was
   being attempted on which object* (see errors.py). Wrapping every
   function in try/except would swallow bugs and bury the real stack
   trace; wrapping the boundaries gives every failure a verbose,
   contextual report exactly once.

3. **The log file is always verbose; the console is quiet.** The
   rotating file at ~/.local/share/netplanner/logs/netplanner.log
   records DEBUG and up so a crash report contains the actions leading
   up to the failure. The console only shows WARNING and up unless
   NETPLANNER_LOG_LEVEL says otherwise, keeping normal GUI runs silent.

Environment variables
---------------------
NETPLANNER_LOG_LEVEL  Console verbosity: DEBUG, INFO, WARNING (default), ERROR
NETPLANNER_LOG_DIR    Override the log directory (used by tests)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from netplanner.permissions import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    restrict_to_owner,
)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 1_000_000  # rotate at ~1 MB
BACKUP_COUNT = 3       # keep netplanner.log.1 … .3


_ROOT_LOGGER_NAME = "netplanner"


def default_log_dir() -> Path:
    """Log directory: $NETPLANNER_LOG_DIR, else XDG data dir like the DB."""
    override = os.environ.get("NETPLANNER_LOG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "netplanner" / "logs"


def setup_logging(
    console_level: int | None = None,
    log_dir: Path | None = None,
) -> logging.Logger:
    """Configure the 'netplanner' logger tree. Safe to call repeatedly.

    Idempotency matters because tests and embedded uses may call this
    more than once; duplicated handlers would double every log line.
    A second call replaces the existing handlers instead of stacking.
    """
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # keep our records out of the root logger

    # Replace rather than append: repeated setup must not duplicate output.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    if console_level is None:
        name = os.environ.get("NETPLANNER_LOG_LEVEL", "WARNING").upper()
        console_level = getattr(logging, name, logging.WARNING)
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler is best-effort: an unwritable log directory must never
    # stop the application from starting — that would invert priorities.
    directory = log_dir or default_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        restrict_to_owner(directory, PRIVATE_DIR_MODE)
        file_handler = logging.handlers.RotatingFileHandler(
            directory / "netplanner.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        restrict_to_owner(directory / "netplanner.log", PRIVATE_FILE_MODE)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Log file unavailable (%s); logging to console only", exc)

    return logger


def log_file_path(log_dir: Path | None = None) -> Path:
    """Where the current log file lives (shown in crash dialogs)."""
    return (log_dir or default_log_dir()) / "netplanner.log"
