"""One database (BUILD.md §2): SQLAlchemy 2 over PostgreSQL+pgvector, or SQLite for the
no-Docker quick start and unit tests. Schema changes go through Alembic (backend/alembic).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import JSON, Engine, String, Text, create_engine, event
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
            return dialect.type_descriptor(postgresql.ARRAY(Text()))
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
_read_engine: Engine | None = None
_read_factory: sessionmaker[Session] | None = None


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
    # No pre-ping: it costs a round trip on every checkout (~8 per request). Instead, the first disconnect error
    # invalidates the whole pool (SQLAlchemy), the API answers that request 503 + Retry-After (main.py) and the
    # web client retries it once; idle connections are recycled after 15 minutes.
    eng = create_engine(url, echo=settings.db_echo, pool_pre_ping=False, pool_recycle=900, pool_size=10,
                        max_overflow=20, json_serializer=json.dumps)

    @event.listens_for(eng, "connect")
    def _pgvector_session(dbapi_conn: Any, _rec: Any) -> None:
        # once per pooled connection instead of per query: HNSW scans keep going past rows the WHERE clause
        # filters out (pgvector 0.8 iterative scans), in exact distance order
        cur = dbapi_conn.cursor()
        cur.execute(f"SET hnsw.iterative_scan = strict_order; SET hnsw.ef_search = {int(settings.hnsw_ef_search)}")
        cur.close()
        dbapi_conn.commit()
    return eng


def engine() -> Engine:
    global _engine, _session_factory
    with _lock:
        if _engine is None:
            _engine = make_engine(settings.resolved_database_url())
            _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engines (tests and demo reset switch databases)."""
    global _engine, _session_factory, _read_engine, _read_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        if _read_engine is not None:
            _read_engine.dispose()
        _engine = None
        _session_factory = None
        _read_engine = None
        _read_factory = None


@contextmanager
def lookup() -> Iterator[Session]:
    """Read-only lookups on the request path (exact cache, semantic cache, pattern kNN, pattern version): an
    autocommit connection, so each query is one round trip (no BEGIN / COMMIT around it)."""
    with engine().connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        with Session(bind=conn, expire_on_commit=False) as s:
            yield s


def read_engine() -> Engine:
    """The read replica (Aurora reader endpoint) when CASCADE_DATABASE_READ_URL is set, else the primary.
    Only dashboard aggregates read from it: they tolerate replica lag of a second or so."""
    global _read_engine, _read_factory
    if not settings.database_read_url:
        return engine()
    with _lock:
        if _read_engine is None:
            _read_engine = make_engine(settings.database_read_url)
            _read_factory = sessionmaker(_read_engine, expire_on_commit=False)
    return _read_engine


@contextmanager
def read_session() -> Iterator[Session]:
    if not settings.database_read_url:
        with session() as s:
            yield s
        return
    read_engine()
    assert _read_factory is not None
    s = _read_factory()
    try:
        yield s
        s.rollback()                     # read-only: never commit on the replica
    finally:
        s.close()


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


def wipe() -> None:
    """Drop everything and migrate from empty (demo build / tests)."""
    from pathlib import Path

    from sqlalchemy import text

    url = settings.resolved_database_url()
    reset_engine()
    if url.startswith("sqlite"):
        path = Path(url.removeprefix("sqlite:///"))
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    else:
        eng = make_engine(url)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        eng.dispose()
    migrate(url)


# ----------------------------------------------------------------------------- Postgres snapshots
def _pg_parts() -> tuple[str, str]:
    url = settings.resolved_database_url()
    base, name = url.rsplit("/", 1)
    return base, name.split("?")[0]


def _pg_admin() -> Engine:
    base, _ = _pg_parts()
    return create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")


def _pg_kill(conn: Any, name: str) -> None:
    from sqlalchemy import text

    conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})


def pg_snapshot_save() -> None:
    """Snapshot = a template copy of the database (CREATE DATABASE ... TEMPLATE)."""
    from sqlalchemy import text

    from .seed import SEED_VERSION

    _, name = _pg_parts()
    reset_engine()
    admin = _pg_admin()
    with admin.connect() as c:
        _pg_kill(c, name)
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}_snapshot"'))
        c.execute(text(f'CREATE DATABASE "{name}_snapshot" TEMPLATE "{name}"'))
        c.execute(text(f"COMMENT ON DATABASE \"{name}_snapshot\" IS '{SEED_VERSION}'"))
    admin.dispose()


def pg_snapshot_exists(version: str) -> bool:
    from sqlalchemy import text

    _, name = _pg_parts()
    admin = _pg_admin()
    with admin.connect() as c:
        got = c.execute(text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = :n"),
                        {"n": f"{name}_snapshot"}).scalar()
    admin.dispose()
    return got == version


def pg_snapshot_restore() -> None:
    from sqlalchemy import text

    _, name = _pg_parts()
    reset_engine()
    admin = _pg_admin()
    with admin.connect() as c:
        _pg_kill(c, name)
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{name}_snapshot"'))
    admin.dispose()
