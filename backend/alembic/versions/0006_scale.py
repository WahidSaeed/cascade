"""Phase 7: ledger rollups (hourly aggregates), and on Postgres the shared pgvector indexes — HNSW on the
semantic cache and the `pattern_vectors` table for the pattern kNN.

Revision ID: 0006_scale
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_scale"
down_revision = "0005_agent"
branch_labels = None
depends_on = None

INT_SUMS = ("input_tokens", "output_tokens", "embed_tokens", "tokens_avoided_in", "tokens_avoided_out",
            "context_avoided_tokens", "prompt_tokens", "context_tokens", "history_tokens", "system_tokens",
            "cache_read_tokens", "cache_write_tokens")
FLOAT_SUMS = ("cost_actual_usd", "cost_avoided_usd", "runtime_cost_usd", "embed_cost_usd", "heuristic_avoided_usd")
COUNTS = ("requests", "uncited", "simulated", "n_cache_exact", "n_pattern_measured", "n_heuristic")


def upgrade() -> None:
    op.create_table(
        "ledger_rollup",
        sa.Column("bucket", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("pattern_id", sa.String(64), primary_key=True),
        sa.Column("route", sa.String(16), primary_key=True),
        *[sa.Column(c, sa.Integer, nullable=False, server_default="0") for c in COUNTS],
        *[sa.Column(c, sa.BigInteger, nullable=False, server_default="0") for c in INT_SUMS],
        *[sa.Column(c, sa.Float, nullable=False, server_default="0") for c in FLOAT_SUMS],
    )
    op.create_table(
        "overhead_rollup",
        sa.Column("bucket", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("pattern_id", sa.String(64), primary_key=True),
        sa.Column("collection_id", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(32), primary_key=True),
        sa.Column("entries", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
    )
    bind = op.get_bind()
    from app.rollups import rebuild_sql

    rebuild_sql(bind)                                   # backfill from the existing raw ledger
    if bind.dialect.name == "postgresql":
        # tuned for the 384-d router embeddings; queries raise ef_search and use iterative scans
        op.execute("CREATE INDEX ix_cache_entries_embedding_hnsw ON cache_entries "
                   "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 128)")
        op.execute("CREATE TABLE pattern_vectors (id bigserial PRIMARY KEY, pattern_id varchar(64) NOT NULL, "
                   "kind varchar(16) NOT NULL, embedding vector(384) NOT NULL)")
        op.execute("CREATE INDEX ix_pattern_vectors_pattern ON pattern_vectors (pattern_id)")
        op.execute("CREATE INDEX ix_pattern_vectors_hnsw ON pattern_vectors "
                   "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 128)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS pattern_vectors")
        op.execute("DROP INDEX IF EXISTS ix_cache_entries_embedding_hnsw")
    op.drop_table("overhead_rollup")
    op.drop_table("ledger_rollup")
