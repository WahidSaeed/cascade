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

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import settings
from .heavy import SYSTEM_PROMPT, LLMRequest, LLMResult, heavy_model, parse_json_reply
from .ledger import OverheadEntry, ledger
from .pricing import cost
from .runtime import ArtifactResult, runtime
from .schemas import Disagreement, Pattern, TaskType, VerifyResult

FALLBACK = "__FALLBACK__"


def _norm(s: str | None) -> str:
    return re.sub(r"[\s.!]+$", "", re.sub(r"\s+", " ", (s or "").strip().lower()))


def agree_exact(a: str, b: str) -> float:
    return 1.0 if _norm(a) == _norm(b) else 0.0


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def agree_transform(a: str, b: str) -> float:
    """Normalized string equality where numbers compare by value ("0.3 m" == "0.30 m")."""
    def canon(s: str) -> str:
        return _NUM.sub(lambda m: f"{float(m.group(0)):g}", s or "")
    return agree_exact(canon(a), canon(b))


def agree_json(a: str, b: str) -> float:
    try:
        ja, jb = parse_json_reply(a), parse_json_reply(b)
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
        j = parse_json_reply(answer)
        if isinstance(j, dict) and "value" in j:
            v = j.get("value")
            return None if v is None else _norm(str(v))
    except (ValueError, TypeError):
        pass
    # a grounded answer cites its sources ("According to the Handbook [1], ... 21 days"): a citation is not the fact
    prose = re.sub(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]", "", answer or "")
    nums = re.findall(r"\d+(?:\.\d+)?", prose)
    return nums[0] if nums else None


def agree_lookup(a: str, b: str) -> float:
    va, vb = lookup_value(a), lookup_value(b)
    return 1.0 if va is not None and va == vb else 0.0


COMPARATORS: dict[str, tuple[str, Callable[[str, str], float]]] = {
    TaskType.classification.value: ("exact", agree_exact),
    TaskType.routing.value: ("exact", agree_exact),
    TaskType.extraction.value: ("json_fields", agree_json),
    TaskType.transform.value: ("normalized_string", agree_transform),
    TaskType.scoring.value: ("numeric_tolerance", agree_numeric),
    TaskType.lookup.value: ("lookup_value", agree_lookup),
}


def comparator(task_type: str | None) -> tuple[str, Callable[[str, str], float]]:
    return COMPARATORS.get(task_type or "", ("exact", agree_exact))


# The answer format per task type. The rule is compiled to it and the reference LLM is asked for it, so the
# comparator sees like with like (a real LLM otherwise answers the same request in a different shape each time).
OUTPUT_FORMATS: dict[str, str] = {
    TaskType.classification.value: "Reply with only the label, in lowercase, with no explanation.",
    TaskType.routing.value: "Reply with only the queue name, in lowercase, with no explanation.",
    TaskType.extraction.value: (
        "Reply with one JSON object and nothing else. Use snake_case keys named after the fields the request "
        "asks for, in the order asked (for example 'order number' -> \"order_number\"). Copy each value exactly "
        "as it appears in the text, including any currency symbol; use null for a field that is missing."),
    TaskType.transform.value: ("Reply with only the result on one line, with no explanation: numbers with exactly "
                               "2 decimals (e.g. 0.30), followed by the target unit or form as written in the "
                               "request."),
    TaskType.scoring.value: "Reply with only the number.",
}


def output_format(task_type: str | None) -> str | None:
    return OUTPUT_FORMATS.get(task_type or "")


def reference_system(pattern: Pattern) -> str:
    fmt = output_format(pattern.task_type.value if pattern.task_type else None)
    return SYSTEM_PROMPT + (f"\n\nOutput format: {fmt}" if fmt else "")


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
    """Default reference: the heavy LLM answers the prompt as on the request path, in the task's output format."""
    def ref(prompt: str) -> Reference:
        res = heavy_model().complete(LLMRequest(question=prompt, system=reference_system(pattern), purpose="verify"))
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

    def one(s: str) -> tuple[ArtifactResult, Reference | None]:
        art = run(pattern, s)
        if art.error is not None or (art.result or "").strip() == FALLBACK:
            return art, None
        return art, ref(s)

    # Samples are independent: run them concurrently (each is an artifact call plus an LLM call), so verifying
    # 40 samples takes a few seconds instead of a minute. Results are scored in sample order.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, settings.verify_concurrency)) as pool:
        results = list(pool.map(one, samples))
    for s, (art, r) in zip(samples, results, strict=True):
        if art.error is not None:
            scored += 1
            disagreements.append(Disagreement(prompt=s, artifact=f"ERROR: {art.error}", llm=None))
            continue
        if r is None:
            fallbacks += 1
            continue
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
