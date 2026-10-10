"""Migrations are frozen, not derived from app.models; this checks they still agree."""
from sqlalchemy import inspect

from app import db
from app.models import Base


def test_migrated_schema_matches_models(fresh_db):
    insp = inspect(db.engine())
    migrated = set(insp.get_table_names()) - {"alembic_version"}
    assert migrated, "no tables migrated"
    db_managed = {"chunks": {"tsv"}}          # Postgres-generated columns the ORM never writes
    for name in migrated:
        model_cols = {c.name for c in Base.metadata.tables[name].columns}
        db_cols = {c["name"] for c in insp.get_columns(name)} - db_managed.get(name, set())
        assert db_cols == model_cols, (name, db_cols ^ model_cols)


def test_every_model_table_is_migrated(fresh_db):
    """Fails until the phase that introduces a table ships its migration."""
    from tests.unit.migration_phases import TABLES_BY_HEAD

    insp = inspect(db.engine())
    assert set(TABLES_BY_HEAD) <= set(insp.get_table_names())
