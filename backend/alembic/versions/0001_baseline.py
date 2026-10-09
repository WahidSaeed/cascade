"""Baseline: users, conversations, patterns, caches, request log, token ledger, overhead.

Enables the pgvector extension on PostgreSQL so later phases can use vector columns.
Table definitions are frozen here (never derived from app.models), so later migrations
apply cleanly on a fresh database.

Revision ID: 0001_baseline
"""
import sqlalchemy as sa
from alembic import op

from app.db import StrList, Vector

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False),
        sa.Column("persona", sa.Boolean, nullable=False),
    )
    op.create_table(
        "user_groups",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("group_id", sa.String(64), primary_key=True),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("collection_ids", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=True),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(64), sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("ts", sa.Float, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=True),
    )
    op.create_table(
        "patterns",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("task_type", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(16), nullable=True),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "cache_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("exact_key", sa.String(64), nullable=False, index=True),
        sa.Column("template_key", sa.String(64), nullable=False, index=True),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("slots", sa.JSON, nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("usage", sa.JSON, nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=True),
        sa.Column("sources", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("hits", sa.Integer, nullable=False),
    )
    op.create_index("ix_cache_scope_user", "cache_entries", ["scope", "user_id"])
    op.create_table(
        "request_logs",
        sa.Column("request_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("ts", sa.Float, nullable=False, index=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("redacted", sa.Text, nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("template_key", sa.String(64), nullable=False, index=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("route", sa.String(16), nullable=False, index=True),
        sa.Column("pattern_id", sa.String(64), nullable=True, index=True),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("collection_ids", sa.JSON, nullable=False),
        sa.Column("knowledge", sa.Boolean, nullable=False),
        sa.Column("light", sa.JSON, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=True),
    )
    op.create_table(
        "ledger",
        sa.Column("request_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("ts", sa.Float, nullable=False, index=True),
        sa.Column("route", sa.String(16), nullable=False, index=True),
        sa.Column("pattern_id", sa.String(64), nullable=True, index=True),
        sa.Column("artifact_kind", sa.String(16), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        *[sa.Column(c, sa.Integer, nullable=False) for c in (
            "input_tokens", "output_tokens", "prompt_tokens", "context_tokens", "history_tokens",
            "system_tokens")],
        sa.Column("split_method", sa.String(32), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False),
        sa.Column("collection_ids", StrList(), nullable=False),
        sa.Column("context_fingerprint", sa.String(64), nullable=True),
        sa.Column("sources", sa.JSON, nullable=False),
        sa.Column("uncited", sa.Boolean, nullable=False),
        sa.Column("embed_tokens", sa.Integer, nullable=False),
        sa.Column("cost_actual_usd", sa.Float, nullable=False),
        sa.Column("tokens_avoided_in", sa.Integer, nullable=False),
        sa.Column("tokens_avoided_out", sa.Integer, nullable=False),
        sa.Column("context_avoided_tokens", sa.Integer, nullable=False),
        sa.Column("cost_avoided_usd", sa.Float, nullable=False),
        sa.Column("runtime_cost_usd", sa.Float, nullable=False),
        sa.Column("estimate_method", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Float, nullable=False),
        sa.Column("simulated", sa.Boolean, nullable=False),
    )
    op.create_table(
        "overhead",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ts", sa.Float, nullable=False, index=True),
        sa.Column("pattern_id", sa.String(64), nullable=True, index=True),
        sa.Column("category", sa.String(32), nullable=False, index=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("collection_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("simulated", sa.Boolean, nullable=False),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("link", sa.Text, nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("read", sa.Boolean, nullable=False),
    )
    op.create_table(
        "runtime_config",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    for name in ["runtime_config", "notifications", "overhead", "ledger", "request_logs", "cache_entries",
                 "patterns", "turns", "conversations", "user_groups", "users"]:
        op.drop_table(name)
