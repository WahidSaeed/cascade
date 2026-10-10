"""Stage 5 of the light layer: the pattern recognizer (BUILD.md §4.1).

Returns (pattern_id | None, probability, margin). A pattern is chosen only when
probability >= pattern_min_prob AND margin over the runner-up >= pattern_min_margin; the
artifact's own input guard and confidence make the final call.

`CentroidRecognizer` (v1): exact template match, then cosine to each approved pattern's
centroid and nearest stored exemplars. Phase 4 adds the trained `ClassifierRecognizer`. Phase 7: on
Postgres the centroid / exemplar similarities come from pgvector (`pattern_vectors`, light/pgindex.py), shared
by every API task; the in-memory computation stays the SQLite / quick-start path.
The `Recognizer` interface lets an external decision model be plugged in later.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import settings
from ..schemas import Pattern
from ..state import on_reset
from . import pgindex
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
    def __init__(self, shared: bool | None = None) -> None:
        self._lock = threading.Lock()
        self._patterns: list[Pattern] = []
        self._shared = shared

    def _use_pg(self) -> bool:
        if self._shared is None:
            from .cache import LightCache

            self._shared = LightCache.shared_index()
        return self._shared

    def refresh(self, patterns: list[Pattern]) -> None:
        with self._lock:
            self._patterns = list(patterns)
        if self._use_pg():
            pgindex.sync_pattern_vectors(patterns)

    def scores(self, norm: Normalized, vector: list[float]) -> list[tuple[str, float, str]]:
        with self._lock:
            patterns = list(self._patterns)
        shared = pgindex.pattern_scores(vector) if patterns and self._use_pg() else None
        out: list[tuple[str, float, str]] = []
        for p in patterns:
            if norm.template_key in p.template_keys:
                out.append((p.id, TEMPLATE_MATCH_PROB, "template"))
                continue
            if shared is not None:
                score, method = shared.get(p.id, (0.0, "centroid"))
                out.append((p.id, score, method))
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


class ClassifierRecognizer(Recognizer):
    """Trained light classifier (BUILD.md §10): logistic regression over the router embeddings, classes =
    approved patterns + `none`, calibrated probabilities, exported to ONNX and run with onnxruntime.
    Exact template matches still win; the centroid recognizer stays as the fallback."""

    NONE = "__none__"

    def __init__(self, onnx_bytes: bytes, classes: list[str], patterns: list[Pattern], meta: dict[str, Any]) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(onnx_bytes, so, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name
        self.classes = classes
        self.meta = meta
        self._templates = {k: p.id for p in patterns for k in p.template_keys}

    def refresh(self, patterns: list[Pattern]) -> None:
        self._templates = {k: p.id for p in patterns for k in p.template_keys}

    def recognize(self, norm: Normalized, vector: list[float]) -> Recognition:
        tmpl = self._templates.get(norm.template_key)
        if tmpl is not None:
            return Recognition(tmpl, TEMPLATE_MATCH_PROB, TEMPLATE_MATCH_PROB, "template", tmpl)
        outs = self._sess.run(None, {self._input: np.asarray([vector], dtype=np.float32)})
        probs = np.asarray(outs[1], dtype=np.float32)[0]
        order = np.argsort(-probs)
        best, second = int(order[0]), (int(order[1]) if len(order) > 1 else None)
        p1 = float(probs[best])
        margin = p1 - (float(probs[second]) if second is not None else 0.0)
        label = self.classes[best]
        if label == self.NONE:
            return Recognition(None, 1.0 - p1, 0.0, "none", None)
        if p1 >= settings.pattern_min_prob and margin >= settings.pattern_min_margin:
            return Recognition(label, p1, margin, "classifier", label)
        return Recognition(None, p1, margin, "none", label)


_lock = threading.Lock()
_recognizer: Recognizer | None = None
# Several API tasks share one database: each rebuilds its recognizer when the approved set changes elsewhere.
_sync: dict[str, Any] = {"signature": None, "checked": 0.0, "rebuilding": False}


def train_classifier(patterns: list[Pattern]) -> ClassifierRecognizer | None:
    """Train on logged prompts labelled by which pattern served them (or would have: its examples), plus
    `none` = LLM-route prompts that no pattern matched."""
    from skl2onnx import to_onnx
    from sklearn.linear_model import LogisticRegression
    from sqlalchemy import select

    from .. import db
    from ..models import RequestLog

    if len(patterns) < settings.recognizer_min_patterns:
        return None
    xs: list[list[float]] = []
    ys: list[str] = []
    with db.session() as s:
        for p in patterns:
            served = [e for (e,) in s.execute(select(RequestLog.embedding).where(RequestLog.pattern_id == p.id,
                                                                                 RequestLog.embedding.is_not(None))
                                              .order_by(RequestLog.ts.desc()).limit(300)) if e]
            rows = served + list(p.exemplars)
            xs += rows
            ys += [p.id] * len(rows)
        logged = s.execute(select(RequestLog.embedding, RequestLog.template_key, RequestLog.light)
                           .where(RequestLog.route == "llm", RequestLog.embedding.is_not(None))
                           .order_by(RequestLog.ts.desc()).limit(1500)).all()
    # LLM-route traffic is labelled by the pattern that WOULD have served it (same template, or close to its
    # centroid / exemplars) — e.g. the prompts discovery clustered before the pattern was approved — and
    # only the rest is `none`. Otherwise the same embeddings would be taught as both classes.
    by_template = {k: p.id for p in patterns for k in p.template_keys}
    none_count = 0
    for emb, tkey, light in logged:
        if not emb or (light or {}).get("stage_hit") not in ("none", None):
            continue
        owner = by_template.get(tkey)
        if owner is None:
            best = max(((max([cosine(emb, p.centroid) if p.centroid else 0.0]
                              + [cosine(emb, e) for e in p.exemplars[:12]]), p.id) for p in patterns), default=(0.0, ""))
            owner = best[1] if best[0] >= settings.pattern_threshold else None
        if owner is not None:
            xs.append(emb)
            ys.append(owner)
        elif none_count < 800:
            xs.append(emb)
            ys.append(ClassifierRecognizer.NONE)
            none_count += 1
    if len(set(ys)) < 2:
        return None
    X = np.asarray(xs, dtype=np.float32)
    clf = LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced")
    clf.fit(X, ys)
    model = to_onnx(clf, X[:1], options={id(clf): {"zipmap": False}}, target_opset=17)
    classes = [str(c) for c in clf.classes_]
    meta = {"trained_at": __import__("time").time(), "samples": len(ys), "classes": len(classes),
            "per_class": {c: ys.count(c) for c in classes}}
    return ClassifierRecognizer(model.SerializeToString(), classes, patterns, meta)


def build_recognizer() -> Recognizer:
    from ..store import store

    patterns = store().approved_patterns()
    try:
        trained = train_classifier(patterns)
    except Exception:  # noqa: BLE001 - never lose routing over a training failure
        trained = None
    if trained is not None:
        return trained
    r = CentroidRecognizer()
    r.refresh(patterns)
    return r


def approved_signature() -> tuple[float, str]:
    """(version, fingerprint): `version` is the latest `updated_at` of any pattern — it only grows, so it orders
    rebuilds; `fingerprint` identifies the approved set (ids + versions) — it changes on every approve / demote /
    reject / edit of a serving pattern, which is what triggers a rebuild."""
    import hashlib

    from sqlalchemy import func, select

    from .. import db
    from ..models import PatternRow

    with db.session() as s:
        latest = s.scalar(select(func.max(PatternRow.updated_at))) or 0.0
        approved = s.execute(select(PatternRow.id, PatternRow.updated_at).where(PatternRow.status == "approved")
                             .order_by(PatternRow.id)).all()
    digest = hashlib.sha256(repr([(i, round(float(u), 6)) for i, u in approved]).encode()).hexdigest()[:16]
    return float(latest), digest


def _build_and_set() -> Recognizer:
    sig = approved_signature()
    r = build_recognizer()
    set_recognizer(r, sig)
    return r


def _rebuild_in_background() -> None:
    try:
        _build_and_set()
    finally:
        _sync["rebuilding"] = False


def recognizer() -> Recognizer:
    import time

    with _lock:
        current = _recognizer
    if current is None:
        with _lock:                      # first use in this process: build synchronously (once)
            if _recognizer is None:
                sig = approved_signature()
                r = build_recognizer()
                _set(r, sig)
            return _recognizer  # type: ignore[return-value]
    now = time.monotonic()
    every = settings.recognizer_refresh_s
    if every > 0 and now - _sync["checked"] >= every and not _sync["rebuilding"]:
        _sync["checked"] = now
        sig, cur = approved_signature(), _sync["signature"]
        if cur is None or sig[1] != cur[1]:
            # another API task changed the approved set: retrain off the request path, keep serving the old
            # recognizer meanwhile (the engine re-checks each pattern's status before it serves)
            _sync["rebuilding"] = True
            threading.Thread(target=_rebuild_in_background, name="recognizer-refresh", daemon=True).start()
    return current


def _set(r: Recognizer | None, sig: tuple[float, str] | None) -> None:
    global _recognizer
    _recognizer = r
    _sync["signature"] = sig
    _sync["checked"] = __import__("time").monotonic()


def set_recognizer(r: Recognizer | None, signature: tuple[float, str] | None = None) -> None:
    with _lock:
        cur = _sync["signature"]
        if r is not None and signature is not None and cur is not None and _recognizer is not None \
                and signature[0] < cur[0]:
            return                         # a slower rebuild from an older state never replaces a newer one
        _set(r, signature)


def refresh_recognizer() -> None:
    """Approve / demote / retire in this task: rebuild (and retrain) now, then hot-swap it in."""
    _build_and_set()


on_reset(lambda: set_recognizer(None))
