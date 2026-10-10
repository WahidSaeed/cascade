"""Light-layer metrics (BUILD.md §4.1): hit/match rates and per-stage latency percentiles over
a rolling window of recent requests, exposed via GET /api/light/metrics."""
from __future__ import annotations

import threading
from collections import Counter, deque
from typing import Any

import numpy as np

from ..state import on_reset

WINDOW = 5000


class LightMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=WINDOW)

    def record(self, stage_hit: str, route: str, latency_by_stage: dict[str, float], recognized: bool,
               artifact_fallback: bool, knowledge: bool, skipped: bool) -> None:
        with self._lock:
            self._events.append({"stage": stage_hit, "route": route, "lat": dict(latency_by_stage),
                                 "recognized": recognized, "fallback": artifact_fallback, "knowledge": knowledge,
                                 "skipped": skipped})

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def snapshot(self, false_hit_rate: float | None = None) -> dict[str, Any]:
        with self._lock:
            ev = list(self._events)
        n = len(ev)
        stages = Counter(e["stage"] for e in ev)
        recognized = sum(e["recognized"] for e in ev)
        fallbacks = sum(e["fallback"] for e in ev)
        lat: dict[str, list[float]] = {}
        for e in ev:
            for k, v in e["lat"].items():
                lat.setdefault(k, []).append(v)

        def rate(x: int, d: int) -> float:
            return round(x / d, 4) if d else 0.0

        return {
            "requests": n,
            "exact_cache_hit_rate": rate(stages["exact_cache"], n),
            "semantic_cache_hit_rate": rate(stages["semantic_cache"], n),
            "recognizer_match_rate": rate(recognized, n),
            "artifact_fallback_rate": rate(fallbacks, recognized),
            "skipped_followups": sum(e["skipped"] for e in ev),
            "knowledge_requests": sum(e["knowledge"] for e in ev),
            "false_hit_rate": false_hit_rate,
            "latency_ms": {k: {"p50": round(float(np.percentile(v, 50)), 3),
                               "p95": round(float(np.percentile(v, 95)), 3), "n": len(v)}
                           for k, v in sorted(lat.items())},
        }


_metrics = LightMetrics()


def light_metrics() -> LightMetrics:
    return _metrics


on_reset(_metrics.reset)
