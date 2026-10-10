"""ORM tables. Every schema change ships with an Alembic migration in backend/alembic/versions."""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, StrList, Vector


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# ----------------------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(128), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    persona: Mapped[bool] = mapped_column(Boolean, default=False)


class UserGroup(Base):
    __tablename__ = "user_groups"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64), primary_key=True)


# ----------------------------------------------------------------------------- conversations
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    collection_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.id", ondelete="CASCADE"),
                                                 index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))           # user | assistant
    text: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)


# ----------------------------------------------------------------------------- patterns
class PatternRow(Base):
    """A pattern and its artifact. Queried fields are columns; the rest lives in `data`."""

    __tablename__ = "patterns"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    task_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


# ----------------------------------------------------------------------------- caches
class CacheEntry(Base):
    __tablename__ = "cache_entries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ce"))
    scope: Mapped[str] = mapped_column(String(16))            # user | global
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exact_key: Mapped[str] = mapped_column(String(64), index=True)
    template_key: Mapped[str] = mapped_column(String(64), index=True)
    template: Mapped[str] = mapped_column(Text)
    slots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prompt: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # original LLM usage + split
    model_id: Mapped[str] = mapped_column(String(128), default="")
    context_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    pattern_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # artifact answers

    __table_args__ = (Index("ix_cache_scope_user", "scope", "user_id"),)


# ----------------------------------------------------------------------------- request log
class RequestLog(Base):
    __tablename__ = "request_logs"
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    prompt: Mapped[str] = mapped_column(Text)                     # raw; deleted by retention
    redacted: Mapped[str] = mapped_column(Text)                   # PII-redacted, used for discovery
    template: Mapped[str] = mapped_column(Text)
    template_key: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    route: Mapped[str] = mapped_column(String(16), index=True)
    pattern_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    answer: Mapped[str] = mapped_column(Text, default="")
    collection_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    knowledge: Mapped[bool] = mapped_column(Boolean, default=False)
    light: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)


# ----------------------------------------------------------------------------- ledger
class LedgerRow(Base):
    __tablename__ = "ledger"
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    route: Mapped[str] = mapped_column(String(16), index=True)
    pattern_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    artifact_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
    history_tokens: Mapped[int] = mapped_column(Integer, default=0)
    system_tokens: Mapped[int] = mapped_column(Integer, default=0)
    split_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    collection_ids: Mapped[list[str]] = mapped_column(StrList, default=list)
    context_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    uncited: Mapped[bool] = mapped_column(Boolean, default=False)
    embed_tokens: Mapped[int] = mapped_column(Integer, default=0)
    embed_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    cost_actual_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_avoided_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_avoided_out: Mapped[int] = mapped_column(Integer, default=0)
    context_avoided_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_avoided_usd: Mapped[float] = mapped_column(Float, default=0.0)
    runtime_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimate_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class OverheadRow(Base):
    __tablename__ = "overhead"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("oh"))
    ts: Mapped[float] = mapped_column(Float, index=True, default=now)
    pattern_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)   # compile|verify|label|training|drift|ingestion|reverify|discovery|agent
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    collection_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


# ----------------------------------------------------------------------------- knowledge (Phase 3)
class CollectionRow(Base):
    __tablename__ = "collections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("col"))
    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(String(64))
    access: Mapped[str] = mapped_column(String(16))           # org | group | private
    group_ids: Mapped[list[str]] = mapped_column(StrList, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("doc"))
    collection_id: Mapped[str] = mapped_column(String(64), ForeignKey("collections.id", ondelete="CASCADE"),
                                               index=True)
    title: Mapped[str] = mapped_column(String(512))
    source_uri: Mapped[str] = mapped_column(Text, default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|parsing|embedding|ready|failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # A re-upload is ingested as a pending version while the current one keeps serving; the swap is atomic.
    pending_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pending_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), default="", server_default="")


class ChunkRow(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("chk"))
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"),
                                             index=True)
    collection_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_version: Mapped[int] = mapped_column(Integer)
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading_path: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    acl: Mapped[list[str]] = mapped_column(StrList, default=list)
    # On Postgres the table also has `tsv`, a generated tsvector (full-text search) owned by the database.


class KbEvent(Base):
    __tablename__ = "kb_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))             # added | updated | deleted
    at: Mapped[float] = mapped_column(Float, default=now)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


# ----------------------------------------------------------------------------- drift (Phase 4)
class DriftSample(Base):
    __tablename__ = "drift_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_id: Mapped[str] = mapped_column(String(64), index=True)
    pattern_version: Mapped[int] = mapped_column(Integer)
    ts: Mapped[float] = mapped_column(Float, index=True, default=now)
    prompt: Mapped[str] = mapped_column(Text)
    artifact: Mapped[str] = mapped_column(Text)
    llm: Mapped[str] = mapped_column(Text)
    agreement: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="live")


# ----------------------------------------------------------------------------- notifications / alerts
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ntf"))
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # None = everyone
    kind: Mapped[str] = mapped_column(String(32))   # pattern_recognized | run_waiting | drift_alert | reverify_alert
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(Text, default="")
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


# ----------------------------------------------------------------------------- agent (Phase 5)
class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    pattern_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)       # intake, iterations, events, script state
    cost_so_far_usd: Mapped[float] = mapped_column(Float, default=0.0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class DatasetRow(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ds"))
    owner: Mapped[str] = mapped_column(String(64))
    s3_uri: Mapped[str] = mapped_column(Text)
    columns_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    use: Mapped[str] = mapped_column(String(16))              # train | eval | both
    rows: Mapped[int] = mapped_column(Integer, default=0)
    pii_status: Mapped[str] = mapped_column(String(16), default="clean")
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class IntegrationRow(Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("int"))
    name: Mapped[str] = mapped_column(String(256))
    type: Mapped[str] = mapped_column(String(16))             # http | aws | module
    target: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))             # feature_source | pre_post | core_model
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_arn: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str] = mapped_column(String(64), default="")
    scan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)


# ----------------------------------------------------------------------------- runtime config (Tools)
class ConfigRow(Base):
    __tablename__ = "runtime_config"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
