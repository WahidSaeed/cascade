"""Verification: does a handler agree with the heavy model? Gate for approval."""
from __future__ import annotations

from .config import settings
from .heavy import heavy_model
from .sandbox import HandlerError, run_handler
from .schemas import Pattern, VerifyResult


def _normalize(s: str) -> str:
    return s.strip().lower()


def verify(pattern: Pattern, samples: list[str]) -> VerifyResult:
    """Run the handler and the heavy model on the same samples; measure agreement.

    '__FALLBACK__' outputs are excluded from scoring — the handler is correctly deferring,
    not answering wrongly.
    """
    samples = samples[: settings.verify_sample_size]
    scored = 0
    agree = 0
    disagreements: list[dict] = []

    for s in samples:
        try:
            handler_out = run_handler(pattern.id, pattern.handler_code, s)
        except HandlerError as e:
            disagreements.append({"prompt": s, "handler": f"ERROR: {e}", "heavy": None})
            scored += 1
            continue

        if _normalize(handler_out) == "__fallback__":
            continue  # correct deferral, not counted

        heavy_out = heavy_model().generate(s)
        scored += 1
        if _normalize(handler_out) == _normalize(heavy_out):
            agree += 1
        else:
            disagreements.append({"prompt": s, "handler": handler_out, "heavy": heavy_out})

    score = (agree / scored) if scored else 0.0
    return VerifyResult(
        pattern_id=pattern.id,
        score=round(score, 4),
        sample_size=scored,
        disagreements=disagreements[:10],
        passed=score >= settings.verify_min_score,
    )
