"""One database (BUILD.md §2): SQLAlchemy 2 over PostgreSQL+pgvector, or SQLite for the
no-Docker quick start and unit tests. Schema changes go through Alembic (backend/alembic).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import JSON, Engine, String, create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator, UserDefinedType

from .config import settings


class _PgVector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            return "[" + ",".join(f"{float(x):.7g}" for x in value) + "]"
        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, str):
                return [float(x) for x in value.strip("[]").split(",") if x]
            return [float(x) for x in value]
        return process


class Vector(TypeDecorator[list[float]]):
    """pgvector `vector(dim)` on Postgres; a JSON array on SQLite."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 384) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector(self.dim))
        return dialect.type_descriptor(JSON())


class StrList(TypeDecorator[list[str]]):
    """text[] on Postgres (GIN-indexable, `&&` overlap filters); JSON on SQLite."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.ARRAY(String()))
        return dialect.type_descriptor(JSON())


class TsVector(TypeDecorator[str]):
    """tsvector on Postgres (full-text search); unused text on SQLite."""

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.TSVECTOR())
        return dialect.type_descriptor(String())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        eng = create_engine(url, echo=settings.db_echo, json_serializer=json.dumps, **kwargs)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return eng
    return create_engine(url, echo=settings.db_echo, pool_pre_ping=True, pool_size=10, max_overflow=20,
                         json_serializer=json.dumps)


def engine() -> Engine:
    global _engine, _session_factory
    with _lock:
        if _engine is None:
            _engine = make_engine(settings.resolved_database_url())
            _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests and demo reset switch databases)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


@contextmanager
def session() -> Iterator[Session]:
    engine()
    assert _session_factory is not None
    s = _session_factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def migrate(url: str | None = None) -> None:
    """Run Alembic migrations to head (idempotent)."""
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    here = Path(__file__).resolve().parents[1]
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    cfg.set_main_option("sqlalchemy.url", (url or settings.resolved_database_url()).replace("%", "%%"))
    command.upgrade(cfg, "head")


def dialect_name() -> str:
    return engine().dialect.name
