"""NetPlanner's exception hierarchy.

Every boundary failure (database, filesystem, export, config import)
is re-raised as one of these, with a message that states what was being
attempted, on which object, and why it failed — so an error dialog or a
log line is diagnosable on its own, without reproducing the problem.

The original exception is always chained with ``raise ... from exc`` so
the full underlying traceback stays in the log.
"""

from __future__ import annotations


class NetPlannerError(Exception):
    """Base for all NetPlanner-raised errors.

    The GUI's slot guard treats these as "expected" failures: their
    messages are written for humans and shown verbatim in the dialog.
    """


class PersistenceError(NetPlannerError):
    """Saving or loading a plan failed (SQLite or .netplan file)."""


class ExportError(NetPlannerError):
    """Rendering a plan to PDF or PNG failed."""


class ConfigImportError(NetPlannerError):
    """Reading a configuration file from disk failed."""
