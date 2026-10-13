"""Pattern version history (admin control centre): one row per artifact version, for history, diffs and rollback.
Existing patterns with an artifact get their current version recorded.

Revision ID: 0007_pattern_versions
"""
import json
import time

import sqlalchemy as sa
from alembic import op

revision = "0007_pattern_versions"
down_revision = "0006_scale"
branch_labels = None
depends_on = None

VOLATILE = ("centroid", "exemplars", "hits")


def upgrade() -> None:
    versions = op.create_table(
        "pattern_versions",
        sa.Column("pattern_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, kind, status, data FROM patterns WHERE kind IS NOT NULL")).fetchall()
    now = time.time()
    for pid, kind, status, data in rows:
        d = json.loads(data) if isinstance(data, str) else dict(data)
        snap = {k: v for k, v in d.items() if k not in VOLATILE}
        op.bulk_insert(versions, [{"pattern_id": pid, "version": int(d.get("version", 1)), "kind": kind,
                                   "status": status, "data": snap, "created_at": now, "updated_at": now}])


def downgrade() -> None:
    op.drop_table("pattern_versions")
