"""P1-07: the same suites run against PostgreSQL + pgvector (docker-compose), and Alembic migrations
run from an empty database. The root conftest gives every test here a fresh Postgres database."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db
from tests.unit.test_components import (  # noqa: F401
    test_cache_ttl_and_rebuild,
    test_config_endpoint,
    test_ledger_aggregates_reconcile,
    test_pattern_and_ledger_endpoints,
)
from tests.unit.test_migrations import *  # noqa: F401,F403

# The same test functions as the SQLite unit suite, collected again here against Postgres.
from tests.unit.test_phase1_acceptance import *  # noqa: F401,F403
from tests.unit.test_review_regressions import *  # noqa: F401,F403
from tests.unit.test_store import *  # noqa: F401,F403

pytestmark = pytest.mark.integration


@pytest.mark.acceptance("P1-07")
def test_migrations_run_from_empty_on_postgres():
    base = os.environ["CASCADE_TEST_DATABASE_URL"]
    name = f"cascade_empty_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.rsplit("/", 1)[0] + f"/{name}"
    try:
        db.migrate(url)
        eng = create_engine(url)
        with eng.connect() as c:
            assert c.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == "vector"
            udt = c.execute(text("SELECT udt_name FROM information_schema.columns "
                                 "WHERE table_name='cache_entries' AND column_name='embedding'")).scalar()
            assert udt == "vector"
        db.migrate(url)                         # idempotent
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.acceptance("P1-07")
def test_running_on_postgres(fresh_db):
    assert db.dialect_name() == "postgresql"


@pytest.mark.acceptance("P1-07")
def test_migrations_run_from_empty_on_sqlite(tmp_path):
    db.migrate(f"sqlite:///{tmp_path / 'empty.db'}")
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert "ledger" in inspect(eng).get_table_names()
