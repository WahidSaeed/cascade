"""Phase 1: cache entries remember which artifact produced them (so they are invalidated with it),
and the ledger records embedding cost separately (net savings subtract it).

Revision ID: 0002_phase1
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_phase1"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cache_entries") as b:
        b.add_column(sa.Column("pattern_id", sa.String(64), nullable=True))
        b.create_index("ix_cache_entries_pattern_id", ["pattern_id"])
    with op.batch_alter_table("ledger") as b:
        b.add_column(sa.Column("embed_cost_usd", sa.Float, nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("ledger") as b:
        b.drop_column("embed_cost_usd")
    with op.batch_alter_table("cache_entries") as b:
        b.drop_index("ix_cache_entries_pattern_id")
        b.drop_column("pattern_id")
