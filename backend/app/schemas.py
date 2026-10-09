"""Pydantic models shared across the API."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
import time
import uuid


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


class Route(str, Enum):
    cache = "cache"
    fast_path = "fast_path"
    heavy = "heavy"


class PatternStatus(str, Enum):
    draft = "draft"        # compiled, not yet verified
    shadow = "shadow"      # verified, running in shadow (logged, not served)
    approved = "approved"  # serving live traffic
    rejected = "rejected"  # explicitly declined


# ---------- chat ----------
class ChatRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    route: Route
    latency_ms: float
    confidence: Optional[float] = None
    pattern_id: Optional[str] = None
    pattern_name: Optional[str] = None


# ---------- patterns ----------
class Pattern(BaseModel):
    id: str = Field(default_factory=lambda: _id("pat"))
    name: str
    description: str = ""
    # a short natural-language description of the request shape this handler covers
    template: str = ""
    # centroid embedding of the cluster this pattern represents
    embedding: list[float] = Field(default_factory=list)
    # the deterministic handler: Python exposing `def handle(prompt: str) -> str`
    handler_code: str = ""
    status: PatternStatus = PatternStatus.draft
    verification_score: Optional[float] = None
    version: int = 1
    hits: int = 0
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)


class PatternEdit(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    template: Optional[str] = None
    handler_code: Optional[str] = None


class VerifyResult(BaseModel):
    pattern_id: str
    score: float
    sample_size: int
    disagreements: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool


# ---------- tool configuration ----------
class ToolConfig(BaseModel):
    # the tunable pieces of the stack, editable from the UI
    heavy_backend: str
    bedrock_model_id: str
    embed_backend: str
    cache_threshold: float
    pattern_threshold: float
    handler_runtime: str
    verify_min_score: float


# ---------- metrics ----------
class Metrics(BaseModel):
    total_requests: int
    by_route: dict[str, int]
    cheap_share: float          # fraction served by cache or fast path
    avg_latency_ms: dict[str, float]
    approved_patterns: int
    draft_patterns: int
