"""Pydantic models shared across the API."""
from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


class Route(StrEnum):
    cache = "cache"
    artifact = "artifact"
    llm = "llm"


class PatternStatus(StrEnum):
    recognized = "recognized"            # found by discovery; waiting for the user
    awaiting_intake = "awaiting_intake"  # user opened intake, not started
    agent_running = "agent_running"
    awaiting_feedback = "awaiting_feedback"
    verifying = "verifying"
    draft = "draft"                      # artifact exists, not verified (or changed)
    shadow = "shadow"                    # verified but not serving (or demoted)
    approved = "approved"                # serving live traffic (deployed)
    rejected = "rejected"
    not_compilable = "not_compilable"
    stopped = "stopped"


class TaskType(StrEnum):
    classification = "classification"
    routing = "routing"
    extraction = "extraction"
    transform = "transform"
    scoring = "scoring"
    lookup = "lookup"


COMPILABLE_TASK_TYPES = {t.value for t in TaskType}


class ArtifactKind(StrEnum):
    rule = "rule"
    ml = "ml"
    dl = "dl"


# ----------------------------------------------------------------------------- chat
class ChatRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None
    collection_ids: list[str] = Field(default_factory=list)


class Source(BaseModel):
    n: int = 0
    document_id: str
    title: str
    page: int | None = None
    chunk_id: str
    snippet: str = ""
    collection_id: str = ""
    version: int = 1


class LightInfo(BaseModel):
    stage_hit: str                                # exact_cache | semantic_cache | recognizer | none | skipped
    template: str
    slots: dict[str, Any] = Field(default_factory=dict)
    prob: float | None = None
    margin: float | None = None
    similarity: float | None = None
    volatile: bool = False
    context_dependent: bool = False
    knowledge: bool = False
    context_fingerprint: str | None = None
    reason: str = ""
    latency_ms_by_stage: dict[str, float] = Field(default_factory=dict)


class TokenInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_tokens: int = 0
    context_tokens: int = 0
    history_tokens: int = 0
    system_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_actual_usd: float = 0.0
    tokens_avoided: int = 0
    cost_avoided_usd: float = 0.0
    estimate_method: str | None = None


class ChatResponse(BaseModel):
    request_id: str
    conversation_id: str
    answer: str
    route: Route
    latency_ms: float
    confidence: float | None = None
    pattern_id: str | None = None
    pattern_name: str | None = None
    artifact_kind: str | None = None
    model_id: str | None = None
    tokens: TokenInfo
    light: LightInfo
    sources: list[Source] = Field(default_factory=list)
    not_fully_deterministic: bool = False


# ----------------------------------------------------------------------------- patterns
class Disagreement(BaseModel):
    prompt: str
    artifact: str | None
    llm: str | None
    expected: str | None = None
    note: str = ""


class VerifyResult(BaseModel):
    pattern_id: str
    score: float
    sample_size: int
    method: str = "exact"
    disagreements: list[Disagreement] = Field(default_factory=list)
    passed: bool
    fallbacks: int = 0
    avg_input_tokens: float | None = None
    avg_output_tokens: float | None = None


class KbDependency(BaseModel):
    document_id: str
    collection_id: str
    version: int
    title: str = ""


class Pattern(BaseModel):
    id: str = Field(default_factory=lambda: _id("pat"))
    name: str
    description: str = ""
    task_type: TaskType | None = None
    status: PatternStatus = PatternStatus.draft
    kind: ArtifactKind | None = None
    handler_code: str = ""                        # rule artifacts: Python with handle(prompt)
    model_uri: str = ""                           # ml/dl artifacts: directory or s3:// prefix
    source_code: str = ""                         # ml/dl artifacts: generated, read-only training + inference Python
    model_card: dict[str, Any] = Field(default_factory=dict)
    templates: list[str] = Field(default_factory=list)        # normalized templates (redacted)
    template_keys: list[str] = Field(default_factory=list)
    centroid: list[float] = Field(default_factory=list)
    exemplars: list[list[float]] = Field(default_factory=list)  # a few stored example embeddings (kNN)
    examples: list[str] = Field(default_factory=list)          # redacted example prompts
    verification: VerifyResult | None = None
    verification_score: float | None = None
    version: int = 1
    hits: int = 0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    samples_measured: int = 0
    leak_warnings: list[str] = Field(default_factory=list)
    kb_dependencies: list[KbDependency] = Field(default_factory=list)
    integration_ids: list[str] = Field(default_factory=list)
    not_fully_deterministic: bool = False
    frequency_per_day: float = 0.0
    projected_tokens_per_day: float = 0.0
    projected_monthly_savings_usd: float = 0.0
    alert: str | None = None
    deployed_function: str | None = None
    approved_by: str | None = None
    owner_id: str | None = None
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)


class PatternEdit(BaseModel):
    name: str | None = None
    description: str | None = None
    handler_code: str | None = None
    task_type: TaskType | None = None


class CompileRequest(BaseModel):
    name: str
    description: str
    task_type: str
    examples: list[str] = Field(default_factory=list)
    kb_dependencies: list[KbDependency] = Field(default_factory=list)
    handler_code: str | None = None               # optional: a reviewer-supplied rule


class VerifyRequest(BaseModel):
    samples: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- config
class ToolConfig(BaseModel):
    values: dict[str, Any]
    editable: list[str]


class ConfigUpdate(BaseModel):
    values: dict[str, Any]
