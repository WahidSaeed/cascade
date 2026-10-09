"""Step 0 smoke test: the app starts, migrations run on an empty SQLite database."""
from sqlalchemy import inspect

from app import db


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_migrations_from_empty(fresh_db):
    tables = set(inspect(db.engine()).get_table_names())
    assert {"patterns", "cache_entries", "request_logs", "ledger", "overhead", "users"} <= tables
