"""Phase 3: knowledge base — collections, documents, chunks (pgvector + full text + ACL), kb_events.

On PostgreSQL: chunks.embedding is vector(384) with an HNSW index, chunks.tsv is a generated
tsvector with a GIN index, and chunks.acl is text[] with a GIN index (access filters run in SQL).

Revision ID: 0003_knowledge
"""
import sqlalchemy as sa
from alembic import op

from app.db import StrList, Vector

revision = "0003_knowledge"
down_revision = "0002_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "collections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("access", sa.String(16), nullable=False),
        sa.Column("group_ids", StrList(), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("collection_id", sa.String(64), sa.ForeignKey("collections.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_uri", sa.Text, nullable=False),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False),
        sa.Column("pending_version", sa.Integer, nullable=True),
        sa.Column("pending_status", sa.String(16), nullable=True),
        sa.Column("pending_sha256", sa.String(64), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False, server_default=""),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("collection_id", sa.String(64), nullable=False, index=True),
        sa.Column("doc_version", sa.Integer, nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("heading_path", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("token_estimate", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("acl", StrList(), nullable=False),
    )
    if pg:
        op.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS "
                   "(to_tsvector('english', coalesce(heading_path, '') || ' ' || text)) STORED")
        op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
        op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
        op.execute("CREATE INDEX ix_chunks_acl ON chunks USING gin (acl)")
    op.create_table(
        "kb_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("collection_id", sa.String(64), nullable=False, index=True),
        sa.Column("document_id", sa.String(64), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("at", sa.Float, nullable=False),
        sa.Column("processed", sa.Boolean, nullable=False),
    )


def downgrade() -> None:
    for t in ("kb_events", "chunks", "documents", "collections"):
        op.drop_table(t)
