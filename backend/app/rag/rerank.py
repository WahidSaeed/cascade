"""Local cross-encoder reranker (BUILD.md §9.3): fastembed `Xenova/ms-marco-MiniLM-L-6-v2` on CPU.
Used only on the LLM path (context assembly), never for the cache key."""
from __future__ import annotations

import threading
from typing import Any

from ..config import settings

_lock = threading.Lock()
_model: Any = None


def _encoder() -> Any:
    global _model
    with _lock:
        if _model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _model = TextCrossEncoder(settings.reranker_model, cache_dir=settings.models_dir, threads=2)
        return _model


def rerank(query: str, texts: list[str]) -> list[float]:
    if not texts:
        return []
    return [float(s) for s in _encoder().rerank(query, texts, batch_size=32)]
