"""Filesystem permissions for the files NetPlanner writes.

A plan carries the device configs attached to it, and a device config
carries enable secrets, SNMP community strings, wireless PSKs and RADIUS
keys. The log names every plan, device and path the user touches, which
is a map of someone's network. Both are written under the process umask,
which on a typical Linux account produces 0755 directories and 0644
files — readable by every other account on the machine.

Nothing here is a substitute for not storing a secret. It narrows the
blast radius of storing one, which is the realistic position for a tool
whose whole job is to hold real device configuration.

This module sits beside errors.py and log.py rather than inside a layer:
persistence and logging both need it, and neither should have to import
the other to get it.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Owner-only. The directory mode is the load-bearing half for SQLite:
# it writes rollback journals and WAL files beside the database,
# transiently and under its own umask, and there is no moment at which
# this code could chmod them. A private directory covers them anyway.
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def restrict_to_owner(path: Path, mode: int) -> None:
    """Narrow a path to its owner, warning rather than failing.

    Filesystems without POSIX permissions — a FAT stick, some network
    mounts — cannot honour this. Refusing to start there would trade a
    real confidentiality gain for a total loss of function, so the
    failure is logged instead: the weaker posture becomes visible rather
    than silently assumed.
    """
    try:
        path.chmod(mode)
    except OSError as exc:
        logger.warning(
            "Could not restrict permissions on %s to %o (%s); it may be "
            "readable by other users on this machine",
            path, mode, exc,
        )
