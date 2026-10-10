"""Phase 5: the model-building agent — agent runs (resumable state), datasets, integrations.

Revision ID: 0005_agent
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_agent"
down_revision = "0004_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pattern_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("cost_so_far_usd", sa.Float, nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.Float, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("s3_uri", sa.Text, nullable=False),
        sa.Column("columns_map", sa.JSON, nullable=False),
        sa.Column("use", sa.String(16), nullable=False),
        sa.Column("rows", sa.Integer, nullable=False),
        sa.Column("pii_status", sa.String(16), nullable=False),
        sa.Column("report", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_table(
        "integrations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("secret_arn", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("scan", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )


def downgrade() -> None:
    for t in ("integrations", "datasets", "agent_runs"):
        op.drop_table(t)
