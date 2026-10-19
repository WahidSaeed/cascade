"""Drift protection (BUILD.md §10): compare artifacts with the LLM on a sample of live traffic.

  sample_live()   `drift_sample_rate` of artifact-served requests are also sent to the LLM, in the
                  background (never on the request's latency path); agreement is logged as overhead.
  run_check()     an explicit drift run (scheduled, or "Run drift check" on /demo): recent artifact
                  traffic is replayed through the deployed artifact and the LLM.
  evaluate()      rolling agreement over the last `drift_window` samples of the current version; below
                  `drift_min_agreement` the artifact is demoted to `shadow` with an alert.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import func, select

from . import db
from .config import settings
from .heavy import LLMRequest, heavy_model
from .models import DriftSample, RequestLog
from .runtime import runtime
from .schemas import Pattern, PatternStatus
from .state import on_reset
from .store import store
from .verifier import comparator, record_llm_overhead, reference_system

log = logging.getLogger("cascade.drift")
MIN_SAMPLES = 10
_rng = random.Random(7)
_rng_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drift")


def should_sample() -> bool:
    if settings.drift_sample_rate <= 0:
        return False
    with _rng_lock:
        return _rng.random() < settings.drift_sample_rate


def sample_live(pattern: Pattern, prompt: str, artifact_answer: str, sync: bool = False) -> None:
    if sync:
        _compare(pattern, prompt, artifact_answer, "live")
    else:
        _pool.submit(_safe_compare, pattern, prompt, artifact_answer)


def _safe_compare(pattern: Pattern, prompt: str, artifact_answer: str) -> None:
    try:
        _compare(pattern, prompt, artifact_answer, "live")
        evaluate(pattern.id)
    except Exception:  # noqa: BLE001 - drift sampling never affects serving
        log.exception("drift sample failed")


def _compare(pattern: Pattern, prompt: str, artifact_answer: str, source: str) -> float:
    if pattern.kb_dependencies:
        # a lookup is compared with the value the LLM extracts from the same retrieved context
        from .lookup import lookup_reference

        llm_text = lookup_reference(pattern, "drift")(prompt).answer
    else:
        res = heavy_model().complete(LLMRequest(question=prompt, system=reference_system(pattern), purpose="verify"))
        record_llm_overhead(res, "drift", pattern.id, note=source)
        llm_text = res.text
    _, agree = comparator(pattern.task_type.value if pattern.task_type else None)
    a = agree(artifact_answer, llm_text)
    with db.session() as s:
        s.add(DriftSample(pattern_id=pattern.id, pattern_version=pattern.version, ts=time.time(), prompt=prompt[:2000],
                          artifact=artifact_answer[:2000], llm=llm_text[:2000], agreement=a, source=source))
    return a


def agreement(pattern_id: str, version: int) -> tuple[float | None, int]:
    with db.session() as s:
        vals = list(s.scalars(select(DriftSample.agreement)
                              .where(DriftSample.pattern_id == pattern_id, DriftSample.pattern_version == version)
                              .order_by(DriftSample.ts.desc(), DriftSample.id.desc()).limit(settings.drift_window)))
    return (sum(vals) / len(vals) if vals else None), len(vals)


def evaluate(pattern_id: str) -> dict[str, Any]:
    from .patterns import demote

    p = store().get_pattern(pattern_id)
    if p is None:
        return {"pattern_id": pattern_id, "status": "missing"}
    score, n = agreement(p.id, p.version)
    demoted = False
    if p.status == PatternStatus.approved and score is not None and n >= MIN_SAMPLES \
            and score < settings.drift_min_agreement:
        demote(p.id, f"Drift: agreement with the LLM fell to {score:.0%} over the last {n} samples "
                     f"(minimum {settings.drift_min_agreement:.0%}); traffic is back on the LLM")
        demoted = True
    return {"pattern_id": p.id, "name": p.name, "agreement": score, "samples": n, "demoted": demoted}


def run_check(pattern_id: str | None = None, n: int = 20) -> list[dict[str, Any]]:
    """Replay recent artifact traffic through the deployed artifact and the LLM, then evaluate."""
    targets = [store().get_pattern(pattern_id)] if pattern_id else store().approved_patterns()
    out = []
    for p in targets:
        if p is None or p.status != PatternStatus.approved:
            continue
        with db.session() as s:
            prompts = list(dict.fromkeys(s.scalars(select(RequestLog.prompt).where(RequestLog.pattern_id == p.id)
                                                   .order_by(RequestLog.ts.desc()).limit(n * 3))))[:n]
        prompts += [e for e in p.examples if e not in prompts][: max(0, n - len(prompts))]
        for prompt in prompts:
            res = runtime().invoke(p, prompt)
            if res.error is None and res.result and res.result.strip() != "__FALLBACK__":
                _compare(p, prompt, res.result, "check")
        out.append(evaluate(p.id))
    return out


def recent_samples(pattern_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(select(DriftSample).where(DriftSample.pattern_id == pattern_id)
                         .order_by(DriftSample.ts.desc()).limit(limit)).all()
        return [{"ts": r.ts, "version": r.pattern_version, "prompt": r.prompt, "artifact": r.artifact, "llm": r.llm,
                 "agreement": r.agreement, "source": r.source} for r in rows]


def false_hit_rate() -> float | None:
    """Share of sampled artifact answers that disagreed with the LLM (light-layer metric)."""
    with db.session() as s:
        n, bad = s.execute(select(func.count(), func.sum(1 - DriftSample.agreement))).one()
    return round(float(bad or 0) / n, 4) if n else None


def _reset() -> None:
    global _rng
    with _rng_lock:
        _rng = random.Random(7)


on_reset(_reset)
