"""SQLite database setup (SQLAlchemy 2.0 style).

The schema stores plans as a row per entity. For simplicity the
scaffold serializes device/link payloads as JSON columns; promote
fields to real columns as querying needs grow.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, String, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from netplanner.errors import PersistenceError

logger = logging.getLogger(__name__)


def default_db_path() -> Path:
    """Where plans live, creating the directory if it is missing.

    This runs during startup, before there is a window to show an error
    in, so a read-only or full home directory has to arrive as a
    PersistenceError naming the path rather than a bare OSError.
    """
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    data_dir = base / "netplanner"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("Could not create the data directory %s", data_dir)
        raise PersistenceError(
            f"Could not create the data directory {data_dir}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return data_dir / "plans.db"


class Base(DeclarativeBase):
    pass


class PlanRow(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    devices: Mapped[list[DeviceRow]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    links: Mapped[list[LinkRow]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # subnets, vlans, sites


class DeviceRow(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    payload: Mapped[dict] = mapped_column(JSON)

    plan: Mapped[PlanRow] = relationship(back_populates="devices")


class LinkRow(Base):
    __tablename__ = "links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    payload: Mapped[dict] = mapped_column(JSON)

    plan: Mapped[PlanRow] = relationship(back_populates="links")


def make_session_factory(db_path: Path | None = None) -> sessionmaker:
    """Open the database and ensure the schema exists.

    create_all() is where an unwritable directory, a file that is not a
    database, or a schema from an incompatible build actually surfaces.
    All three are the same thing to the caller — the database could not
    be opened — so they are reported as one PersistenceError naming it.
    """
    path = db_path or default_db_path()
    logger.debug("Opening SQLite engine at %s", path)
    try:
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
    except (SQLAlchemyError, OSError) as exc:
        logger.exception("Could not open the database at %s", path)
        raise PersistenceError(
            f"Could not open the database {path}: {type(exc).__name__}: {exc}"
        ) from exc
    return sessionmaker(bind=engine, expire_on_commit=False)
