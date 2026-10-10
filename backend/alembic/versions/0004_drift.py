"""Phase 4: drift samples (artifact vs LLM on a sample of live traffic).

Revision ID: 0004_drift
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_drift"
down_revision = "0003_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_samples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pattern_id", sa.String(64), nullable=False, index=True),
        sa.Column("pattern_version", sa.Integer, nullable=False),
        sa.Column("ts", sa.Float, nullable=False, index=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("artifact", sa.Text, nullable=False),
        sa.Column("llm", sa.Text, nullable=False),
        sa.Column("agreement", sa.Float, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),          # live | check
    )


def downgrade() -> None:
    op.drop_table("drift_samples")
