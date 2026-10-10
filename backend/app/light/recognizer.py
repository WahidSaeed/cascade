"""Stage 5 of the light layer: the pattern recognizer (BUILD.md §4.1).

Returns (pattern_id | None, probability, margin). A pattern is chosen only when
probability >= pattern_min_prob AND margin over the runner-up >= pattern_min_margin; the
artifact's own input guard and confidence make the final call.

`CentroidRecognizer` (v1): exact template match, then cosine to each approved pattern's
centroid and nearest stored exemplars. Phase 4 adds the trained `ClassifierRecognizer`.
The `Recognizer` interface lets an external decision model be plugged in later.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import settings
from ..schemas import Pattern
from ..state import on_reset
from .normalize import Normalized
from .vec import cosine

TEMPLATE_MATCH_PROB = 0.99


@dataclass(frozen=True)
class Recognition:
    pattern_id: str | None
    prob: float
    margin: float
    method: str = "none"           # template | centroid | knn | classifier | none
    best_id: str | None = None     # the top candidate even when below the gates (for logging)


class Recognizer(ABC):
    @abstractmethod
    def recognize(self, norm: Normalized, vector: list[float]) -> Recognition: ...

    @abstractmethod
    def refresh(self, patterns: list[Pattern]) -> None:
        """Reload the approved patterns (called on approve / demote / retire)."""


class CentroidRecognizer(Recognizer):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._patterns: list[Pattern] = []

    def refresh(self, patterns: list[Pattern]) -> None:
        with self._lock:
            self._patterns = list(patterns)

    def scores(self, norm: Normalized, vector: list[float]) -> list[tuple[str, float, str]]:
        with self._lock:
            patterns = list(self._patterns)
        out: list[tuple[str, float, str]] = []
        for p in patterns:
            if norm.template_key in p.template_keys:
                out.append((p.id, TEMPLATE_MATCH_PROB, "template"))
                continue
            c = cosine(vector, p.centroid) if p.centroid else 0.0
            k = max((cosine(vector, e) for e in p.exemplars), default=0.0)
            out.append((p.id, max(c, k), "centroid" if c >= k else "knn"))
        return sorted(out, key=lambda s: -s[1])

    def recognize(self, norm: Normalized, vector: list[float]) -> Recognition:
        ranked = self.scores(norm, vector)
        if not ranked:
            return Recognition(None, 0.0, 0.0)
        best_id, prob, method = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = prob - runner_up
        # For the centroid recognizer the "probability" is the similarity score itself, so the
        # similarity threshold (pattern_threshold) and pattern_min_prob both apply.
        min_prob = max(settings.pattern_min_prob, settings.pattern_threshold)
        if prob >= min_prob and margin >= settings.pattern_min_margin:
            return Recognition(best_id, prob, margin, method, best_id)
        return Recognition(None, prob, margin, "none", best_id)


_lock = threading.Lock()
_recognizer: Recognizer | None = None


def recognizer() -> Recognizer:
    global _recognizer
    with _lock:
        if _recognizer is None:
            from ..store import store

            r = CentroidRecognizer()
            r.refresh(store().approved_patterns())
            _recognizer = r
        return _recognizer


def set_recognizer(r: Recognizer | None) -> None:
    global _recognizer
    with _lock:
        _recognizer = r


def refresh_recognizer() -> None:
    from ..store import store

    recognizer().refresh(store().approved_patterns())


on_reset(lambda: set_recognizer(None))
