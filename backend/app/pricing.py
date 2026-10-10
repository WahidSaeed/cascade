"""Model prices (BUILD.md §4.2): configured in `model_prices`, never hard-coded at call sites."""
from __future__ import annotations

from .config import settings


def _price(model_id: str | None) -> dict[str, float]:
    prices = settings.model_prices
    if model_id and model_id in prices:
        return prices[model_id]
    return prices.get(settings.heavy_model_id, prices["demo-llm"])


def cost(model_id: str | None, input_tokens: int, output_tokens: int, cache_read: int = 0,
         cache_write: int = 0) -> float:
    """USD for one LLM call. `input_tokens` is the total input, including prompt-cache read/write
    tokens; those are billed at their own rates and the rest at the input rate."""
    p = _price(model_id)
    uncached = max(0, input_tokens - cache_read - cache_write)
    usd = (uncached * p.get("input", 0.0) + output_tokens * p.get("output", 0.0)
           + cache_read * p.get("cache_read", p.get("input", 0.0))
           + cache_write * p.get("cache_write", p.get("input", 0.0))) / 1_000_000
    return round(usd, 8)


def embed_cost(model_id: str, tokens: int) -> float:
    return round(tokens * settings.model_prices.get(model_id, {}).get("input", 0.0) / 1_000_000, 8)


def lambda_cost(duration_ms: float, memory_mb: int = 256) -> float:
    """Artifact runtime cost: GB-seconds plus the per-request charge (an estimate for local runs)."""
    p = settings.model_prices.get("_lambda", {})
    gb_s = (memory_mb / 1024) * max(duration_ms, 1.0) / 1000
    return round(gb_s * p.get("gb_second", 0.0) + p.get("request", 0.0), 10)
