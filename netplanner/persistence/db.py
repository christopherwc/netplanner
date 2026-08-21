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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def default_db_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    data_dir = base / "netplanner"
    data_dir.mkdir(parents=True, exist_ok=True)
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


logger = logging.getLogger(__name__)


def make_session_factory(db_path: Path | None = None) -> sessionmaker:
    path = db_path or default_db_path()
    logger.debug("Opening SQLite engine at %s", path)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
