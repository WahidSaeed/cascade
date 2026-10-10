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
from tests.unit.test_phase3_acceptance import *  # noqa: F401,F403
from tests.unit.test_phase4_acceptance import *  # noqa: F401,F403
from tests.unit.test_phase5_acceptance import *  # noqa: F401,F403
from tests.unit.test_rag_components import *  # noqa: F401,F403
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


@pytest.mark.acceptance("P3-02")
def test_pgvector_access_filter_is_in_sql(fresh_db):
    """The ACL predicate is part of the SQL: a principal set with no grant gets zero rows from pgvector."""
    from app import seed
    from app.light.embedder import get_embedder
    from app.rag.retriever import PgVectorRetriever

    seed.seed_users()
    seed.seed_knowledge()
    cols = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]
    vec, _ = get_embedder().embed("bonus range for team leads")
    r = PgVectorRetriever()
    assert r.candidates(vec, "bonus range", ["user:nobody"], cols, 30) == []
    manager = r.candidates(vec, "bonus range", ["group:managers"], cols, 30)
    assert manager and {c.collection_id for c in manager} == {seed.collection_id("compensation")}
    with db.engine().connect() as c:
        plan = "\n".join(x[0] for x in c.execute(text(
            "EXPLAIN SELECT id FROM chunks WHERE acl && ARRAY['org:*']::text[] ORDER BY id")))
    assert "acl" in plan
