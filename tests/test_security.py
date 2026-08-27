"""What the application refuses to do with the files it touches.

Confidentiality of what it writes: a plan carries device configs, and a
device config carries secrets, so the database and the log are the
owner's business and nobody else's.
"""

from __future__ import annotations

import logging
import stat

from netplanner.permissions import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    restrict_to_owner,
)
from netplanner.persistence.db import default_db_path, make_engine


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ------------------------------------------------- confidentiality at rest
def test_the_database_and_its_directory_are_owner_only(tmp_path, monkeypatch):
    """The database holds attached device configs, and those hold enable
    secrets and community strings. Under the default umask it would be
    world-readable on a shared machine."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db_path = default_db_path()
    make_engine(db_path).dispose()

    assert _mode(db_path.parent) == PRIVATE_DIR_MODE
    assert _mode(db_path) == PRIVATE_FILE_MODE


def test_a_data_directory_from_an_older_build_is_narrowed(tmp_path, monkeypatch):
    """mkdir's mode applies only when it creates the directory, so a
    directory left behind by a build that predates this has to be
    chmod'ed rather than assumed."""
    stale = tmp_path / "netplanner"
    stale.mkdir(mode=0o755)
    assert _mode(stale) == 0o755

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    default_db_path()
    assert _mode(stale) == PRIVATE_DIR_MODE


def test_the_log_and_its_directory_are_owner_only(tmp_path):
    """The log names every plan, device and path the user touches."""
    from netplanner.log import setup_logging

    log_dir = tmp_path / "logs"
    logger = setup_logging(log_dir=log_dir)
    try:
        assert _mode(log_dir) == PRIVATE_DIR_MODE
        assert _mode(log_dir / "netplanner.log") == PRIVATE_FILE_MODE
    finally:
        # Close, not just detach: the suite runs with warnings as errors,
        # so a leaked file handle fails the run rather than lingering.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_a_filesystem_that_cannot_chmod_warns_instead_of_failing(tmp_path, caplog):
    """A FAT stick or some network mounts cannot honour a mode. Refusing
    to start there would trade confidentiality for total loss of
    function, so the weaker posture is logged rather than assumed."""
    missing = tmp_path / "not-there"
    with caplog.at_level(logging.WARNING, logger="netplanner.permissions"):
        restrict_to_owner(missing, PRIVATE_FILE_MODE)
    assert "readable by other users" in caplog.text
