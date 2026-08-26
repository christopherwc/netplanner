"""Shared fixtures.

The one autouse fixture here closes database handles. A PlanRepository
owns a SQLAlchemy engine whose pool keeps the SQLite file open until
dispose() is called; tests create one per test and mostly do not close
it. On Python 3.13+ the interpreter reports each of those as
"ResourceWarning: unclosed database" when the connection is finalized
during garbage collection, and because the suite runs with warnings as
errors, those surface as failures attached to whichever unlucky test
happened to trigger the collection — never the test that opened the
handle. This makes the cleanup deterministic instead.

Production code closes its own repository (AppController.close, called
from main); this is only for tests, which open a great many of them.
"""

from __future__ import annotations

import pytest

from netplanner.persistence.repository import PlanRepository


@pytest.fixture(autouse=True)
def close_repositories(monkeypatch):
    """Dispose every engine opened during a test, in reverse order."""
    opened: list[PlanRepository] = []
    original_init = PlanRepository.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(PlanRepository, "__init__", tracking_init)
    yield
    for repository in reversed(opened):
        repository.close()
