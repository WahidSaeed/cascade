"""Central configuration. Everything switches between local dev and AWS by env var."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CASCADE_", env_file=".env", extra="ignore")

    # --- storage ---
    # "sqlite" for local dev, "dynamodb" for AWS.
    store_backend: str = "sqlite"
    sqlite_path: str = "cascade.db"
    dynamo_table_patterns: str = "cascade-patterns"
    dynamo_table_cache: str = "cascade-cache"
    dynamo_table_logs: str = "cascade-logs"

    # --- heavy model (System 2) ---
    # "mock" runs with no credentials (canned replies) so the whole flow works offline.
    # "bedrock" calls Amazon Bedrock InvokeModel.
    heavy_backend: str = "mock"
    bedrock_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_embed_model_id: str = "amazon.titan-embed-text-v2:0"

    # --- router / embeddings (System 1) ---
    # "local" = deterministic offline embedder; "bedrock" = Titan/Cohere embeddings.
    embed_backend: str = "local"
    embed_dim: int = 256

    # --- routing thresholds (cosine similarity, 0..1) ---
    # NOTE: tuned for the local hashing embedder. With real Bedrock embeddings, which
    # cluster much tighter, raise these (cache ~0.97, pattern ~0.88) and re-check.
    cache_threshold: float = 0.95
    pattern_threshold: float = 0.72

    # --- fast-path execution ---
    # "subprocess" = restricted local subprocess (DEV ONLY, not a security boundary).
    # "lambda" = invoke one AWS Lambda per approved handler (production).
    handler_runtime: str = "subprocess"
    handler_timeout_s: float = 2.0
    lambda_name_prefix: str = "cascade-handler-"

    # --- verification ---
    # Minimum agreement with the heavy model before a handler may be approved.
    verify_min_score: float = 0.9
    verify_sample_size: int = 20


settings = Settings()
