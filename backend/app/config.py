"""Central configuration (BUILD.md §15): every backend and threshold is a CASCADE_* env var.

Never hard-code region, model, thresholds or prices anywhere else.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# USD per 1M tokens. Placeholders shaped like current Bedrock list prices; whoever deploys
# fills in the real values from the AWS pricing pages (DEPLOY.md). Configured, not hard-coded:
# override the whole table with CASCADE_MODEL_PRICES='{"model-id": {...}}'.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "demo-llm": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {"input": 3.0, "output": 15.0, "cache_read": 0.30,
                                                      "cache_write": 3.75},
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 1.0, "output": 5.0, "cache_read": 0.10,
                                                     "cache_write": 1.25},
    "demo-light-llm": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "amazon.titan-embed-text-v2:0": {"input": 0.02, "output": 0.0},
    "local-embedder": {"input": 0.0, "output": 0.0},
    "_lambda": {"gb_second": 0.0000166667, "request": 0.0000002},
    "_rerank": {"per_query": 0.0},
    "_training": {"per_hour": 0.115},      # ml.m5.large on-demand (local runs are priced the same, as an estimate)
}


def _cpus(cap: int) -> int:
    import os

    affinity = getattr(os, "sched_getaffinity", None)   # Linux / Fargate: the CPUs this task may use
    n = len(affinity(0)) if affinity else (os.cpu_count() or 1)
    return max(1, min(cap, n))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASCADE_", env_file=".env", extra="ignore",
                                      validate_assignment=True)

    # --- environment ---
    env: str = "dev"                       # dev | test | prod
    demo: bool = False                     # /demo + demo endpoints (compose / e2e set true; never prod)
    auth: str = "local"                    # local (X-User-Id / persona switcher) | cognito
    cors_origins: str = "*"                # comma-separated; prod = the CloudFront origin only
    models_dir: str = str(REPO_ROOT / ".models")
    data_dir: str = str(REPO_ROOT / "backend" / "data")
    demo_dir: str = str(REPO_ROOT / "demo")

    # --- database (one SqlStore; SQLite for quick start/unit tests, Postgres elsewhere) ---
    database_url: str = ""                 # empty -> sqlite file under data_dir
    database_read_url: str = ""            # Phase 7: Aurora reader endpoint for dashboard reads (empty -> primary)
    pgvector_index: bool = True            # Phase 7: on Postgres, semantic cache + pattern kNN query pgvector
    hnsw_ef_search: int = 64
    exact_cache_url: str = ""              # Phase 7: rediss://… (ElastiCache) hot exact cache; empty -> database only
    exact_cache_ttl_s: int = 300           # hot copies live at most this long (never past the entry's expiry)
    config_refresh_s: float = 2.0          # how often an API task picks up config edits made by another (<=0: never)
    recognizer_refresh_s: float = 2.0      # how often an API task checks whether another task changed the patterns (<=0: never)
    db_echo: bool = False

    # --- heavy model (System 2) ---
    llm: str = "demo"                      # demo | bedrock
    bedrock_region: str = "us-east-1"
    # Current Claude models on Bedrock are invoked through cross-region inference profiles ("us." ...).
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    light_llm_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    llm_max_tokens: int = 1024
    compile_max_tokens: int = 4096         # a generated rule with its tables does not fit in llm_max_tokens
    prompt_caching: bool = True            # mark the stable system block as a Bedrock cache point
    demo_latency_ms: int = 2500            # Demo LLM simulated latency centre (0 in tests)
    crm_url: str = "http://127.0.0.1:8090"     # crm-mock (S9 "CRM tier lookup"); compose: http://crm-mock:8090
    demo_reasoning_overhead_tokens: int = 40

    # --- light layer (System 1) ---
    embed_backend: str = "fastembed"       # fastembed | multilingual-e5-small | bedrock-titan | hashing
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    embed_threads: int = Field(default_factory=lambda: _cpus(4))  # ONNX intra-op threads: available CPUs, max 4
    titan_embed_model_id: str = "amazon.titan-embed-text-v2:0"
    cache_threshold: float = 0.95
    pattern_threshold: float = 0.80
    pattern_min_prob: float = 0.80
    pattern_min_margin: float = 0.05
    recognizer_min_patterns: int = 3
    cache_ttl_s: int = 7 * 24 * 3600
    volatile_ttl_s: int = 0                # 0 = volatile prompts are never cached
    global_cache: bool = True              # PII-free prompts may be shared across users
    followup_short_words: int = 4          # a prompt this short in an ongoing conversation is a follow-up

    # --- artifacts ---
    artifact_runtime: str = "subprocess"   # subprocess (dev only) | lambda
    artifact_timeout_s: float = 5.0
    artifact_min_confidence: float = 0.6
    artifact_role_arn: str = ""
    artifact_bucket: str = ""
    lambda_prefix: str = "cascade-artifact-"
    ml_runtime_image: str = ""             # ECR image URI of the ml-runtime Lambda image
    dl_runtime_image: str = ""             # ECR image URI of the dl-runtime Lambda image
    artifact_subnet_ids: str = ""          # private subnets for artifact Lambdas (comma-separated)
    artifact_integration_sg: str = ""      # egress-restricted security group for artifacts with integrations
    artifact_sg: str = ""                  # Phase 7: security group for every other artifact (S3 endpoint only)
    ml_min_samples: int = 200
    dl_min_samples: int = 1000

    # --- verification / approval ---
    verify_min_score: float = 0.9
    verify_sample_size: int = 40
    scoring_tolerance: float = 1.0

    # --- ledger / pricing ---
    model_prices: dict[str, dict[str, float]] = Field(default_factory=lambda: dict(DEFAULT_PRICES))
    log_retention_days: int = 30

    # --- knowledge / RAG ---
    retriever: str = "auto"                # auto | pgvector | memory | bedrock-kb
    chunk_tokens: int = 500
    chunk_overlap: int = 60
    rag_candidates: int = 30
    rag_fingerprint_k: int = 8
    rag_relevance_floor: float = 0.60      # cosine: below this a chunk is never "relevant"
    rag_relevance_delta: float = 0.06      # relevant = within this of the best match (and above the floor)
    rag_top_k: int = 5
    rag_max_context_tokens: int = 3000
    rag_rerank_min: float = 0.0
    rag_no_context: str = "say_not_found"  # say_not_found | answer_without_context
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    history_max_tokens: int = 800
    bedrock_kb_id: str = ""
    ingest_queue_url: str = ""             # empty -> in-process background task
    blob_bucket: str = ""                  # empty -> local filesystem under data_dir/blobs

    # --- discovery / drift ---
    discovery_min: int = 25
    discovery_similarity: float = 0.80    # prompt-to-cluster-centroid cosine (bge-small)
    drift_sample_rate: float = 0.05
    drift_min_agreement: float = 0.85
    drift_window: int = 50
    scheduler: bool = False                # in-process discovery/drift schedule (local stand-in for EventBridge)

    # --- agent ---
    agent_llm: str = "scripted"            # scripted (demo) | bedrock
    agent_max_iterations: int = 5
    agent_max_cost_usd: float = 5.0
    agent_autostart: bool = False
    agent_executor: str = "inline"         # inline (thread in this process) | worker (the agent-worker task)
    dl_trainer: str = "local"              # local | sagemaker
    dl_base_model: str = "prajjwal1/bert-tiny"
    dl_training_image: str = ""            # ECR image with the training script (SageMaker)
    dl_instance_type: str = "ml.m5.large"
    sagemaker_role_arn: str = ""
    source_train_timeout_s: int = 900      # user-edited ml/dl training code: wall and CPU limit per job
    source_train_max_file_mb: int = 512    # ... and the largest file it may write
    source_train_max_memory_mb: int = 8192 # ... and its address space (Linux; torch maps a lot of virtual memory)

    # --- privacy ---
    pii_backend: str = "regex"             # regex | comprehend

    # --- auth (Phase 6) ---
    cognito_issuer: str = ""
    cognito_audience: str = ""
    cognito_jwks_url: str = ""
    admin_group: str = "admins"

    @field_validator("model_prices", mode="before")
    @classmethod
    def _parse_prices(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = json.loads(v)
        if isinstance(v, dict):
            merged = dict(DEFAULT_PRICES)
            merged.update(v)
            return merged
        return v

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{Path(self.data_dir) / 'cascade.db'}"

    @property
    def is_postgres(self) -> bool:
        return self.resolved_database_url().startswith("postgresql")

    @property
    def heavy_model_id(self) -> str:
        return "demo-llm" if self.llm == "demo" else self.bedrock_model_id


settings = Settings()

# Fields the Tools view may change at runtime (PUT /api/config). Everything else is deploy-time.
EDITABLE = {
    "llm", "bedrock_model_id", "embed_backend", "cache_threshold", "pattern_threshold",
    "pattern_min_prob", "pattern_min_margin", "artifact_runtime", "artifact_min_confidence",
    "verify_min_score", "model_prices", "global_cache", "ml_min_samples", "dl_min_samples",
    "drift_sample_rate", "drift_min_agreement", "rag_max_context_tokens", "rag_top_k",
    "agent_max_iterations", "agent_max_cost_usd", "demo_latency_ms",
}
