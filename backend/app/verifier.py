"""Verification (BUILD.md §10, rule 3): does an artifact agree with the heavy LLM?

The artifact runs out of process (ArtifactRuntime); the LLM answers the same prompts. Every
LLM call is recorded as `verify` (or `reverify`) overhead, and the measured LLM token usage
becomes the pattern's `pattern_measured` estimate. Per task type:
  classification, routing  normalized exact match
  extraction               JSON field-level agreement
  transform                normalized string equality
  scoring                  numeric agreement within `scoring_tolerance`
  lookup                   the artifact's value equals the value the LLM extracts, as structured output,
                           from the same retrieved context (every fact must match: threshold 1.0)
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import settings
from .heavy import LLMRequest, LLMResult, heavy_model
from .ledger import OverheadEntry, ledger
from .pricing import cost
from .runtime import ArtifactResult, runtime
from .schemas import Disagreement, Pattern, TaskType, VerifyResult

FALLBACK = "__FALLBACK__"


def _norm(s: str | None) -> str:
    return re.sub(r"[\s.!]+$", "", re.sub(r"\s+", " ", (s or "").strip().lower()))


def agree_exact(a: str, b: str) -> float:
    return 1.0 if _norm(a) == _norm(b) else 0.0


def agree_json(a: str, b: str) -> float:
    try:
        ja, jb = json.loads(a), json.loads(b)
    except (ValueError, TypeError):
        return agree_exact(a, b)
    if not isinstance(ja, dict) or not isinstance(jb, dict):
        return 1.0 if ja == jb else 0.0
    keys = set(ja) | set(jb)
    if not keys:
        return 1.0

    def nv(v: Any) -> str:
        return _norm(str(v)) if v is not None else ""

    return sum(nv(ja.get(k)) == nv(jb.get(k)) for k in keys) / len(keys)


def agree_numeric(a: str, b: str) -> float:
    try:
        fa = float(re.findall(r"-?\d+(?:\.\d+)?", a)[0])
        fb = float(re.findall(r"-?\d+(?:\.\d+)?", b)[0])
    except (IndexError, ValueError):
        return 0.0
    return 1.0 if abs(fa - fb) <= settings.scoring_tolerance else 0.0


def lookup_value(answer: str) -> str | None:
    """The fact an answer states: a JSON {"value": ...} or the first number in prose."""
    try:
        j = json.loads(answer)
        if isinstance(j, dict):
            v = j.get("value")
            return None if v is None else _norm(str(v))
    except (ValueError, TypeError):
        pass
    nums = re.findall(r"\d+(?:\.\d+)?", answer or "")
    return nums[0] if nums else None


def agree_lookup(a: str, b: str) -> float:
    va, vb = lookup_value(a), lookup_value(b)
    return 1.0 if va is not None and va == vb else 0.0


COMPARATORS: dict[str, tuple[str, Callable[[str, str], float]]] = {
    TaskType.classification.value: ("exact", agree_exact),
    TaskType.routing.value: ("exact", agree_exact),
    TaskType.extraction.value: ("json_fields", agree_json),
    TaskType.transform.value: ("normalized_string", agree_exact),
    TaskType.scoring.value: ("numeric_tolerance", agree_numeric),
    TaskType.lookup.value: ("lookup_value", agree_lookup),
}


def comparator(task_type: str | None) -> tuple[str, Callable[[str, str], float]]:
    return COMPARATORS.get(task_type or "", ("exact", agree_exact))


def threshold_for(pattern: Pattern) -> float:
    # A lookup artifact states facts: one wrong fact is a failure.
    return 1.0 if pattern.task_type == TaskType.lookup else settings.verify_min_score


@dataclass
class Reference:
    """What the LLM says for one sample, and how to compare (lookup uses structured extraction)."""
    answer: str
    result: LLMResult | None


ReferenceFn = Callable[[str], Reference]


def llm_reference(pattern: Pattern, category: str, run_id: str | None = None) -> ReferenceFn:
    """Default reference: the heavy LLM answers the prompt exactly as on the request path."""
    def ref(prompt: str) -> Reference:
        res = heavy_model().complete(LLMRequest(question=prompt, purpose="verify"))
        record_llm_overhead(res, category, pattern.id, run_id)
        return Reference(res.text, res)
    return ref


def record_llm_overhead(res: LLMResult, category: str, pattern_id: str | None, run_id: str | None = None,
                        collection_id: str | None = None, note: str = "") -> float:
    usd = cost(res.model_id, res.input_tokens, res.output_tokens, res.cache_read_tokens, res.cache_write_tokens)
    ledger().record_overhead(OverheadEntry(category=category, input_tokens=res.input_tokens,
                                           output_tokens=res.output_tokens, cost_usd=usd, model_id=res.model_id,
                                           pattern_id=pattern_id, run_id=run_id, collection_id=collection_id,
                                           note=note))
    return usd


def verify(pattern: Pattern, samples: list[str], reference: ReferenceFn | None = None,
           expected: dict[str, str] | None = None, category: str = "verify",
           invoke: Callable[[Pattern, str], ArtifactResult] | None = None) -> VerifyResult:
    """Score agreement between the artifact and the reference on `samples`.

    `expected` (user ground-truth labels) takes precedence over the LLM where present.
    `__FALLBACK__` outputs are a correct deferral and are not scored, but a pattern must still
    answer at least half of the samples itself to pass.
    """
    samples = list(dict.fromkeys(samples))[: settings.verify_sample_size]
    method, agree = comparator(pattern.task_type.value if pattern.task_type else None)
    ref = reference or llm_reference(pattern, category)
    run = invoke or runtime().invoke
    total = 0.0
    scored = fallbacks = 0
    in_tok: list[int] = []
    out_tok: list[int] = []
    disagreements: list[Disagreement] = []
    for s in samples:
        art = run(pattern, s)
        if art.error is not None:
            scored += 1
            disagreements.append(Disagreement(prompt=s, artifact=f"ERROR: {art.error}", llm=None))
            continue
        if (art.result or "").strip() == FALLBACK:
            fallbacks += 1
            continue
        r = ref(s)
        if r.result is not None:
            in_tok.append(r.result.input_tokens)
            out_tok.append(r.result.output_tokens)
        truth = (expected or {}).get(s)
        target = truth if truth is not None else r.answer
        a = agree(art.result or "", target)
        total += a
        scored += 1
        if a < 1.0:
            disagreements.append(Disagreement(prompt=s, artifact=art.result, llm=r.answer, expected=truth))
    score = total / scored if scored else 0.0
    coverage = scored / len(samples) if samples else 0.0
    return VerifyResult(
        pattern_id=pattern.id, score=round(score, 4), sample_size=scored, method=method,
        disagreements=disagreements[:20], passed=score >= threshold_for(pattern) and coverage >= 0.5,
        fallbacks=fallbacks,
        avg_input_tokens=round(sum(in_tok) / len(in_tok), 2) if in_tok else None,
        avg_output_tokens=round(sum(out_tok) / len(out_tok), 2) if out_tok else None,
    )
