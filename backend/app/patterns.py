"""Pattern compilation: turn a recurring request shape into a deterministic handler.

In production the heavy model writes the handler. Offline (mock), we emit a small, real
handler so the review -> verify -> approve -> route flow is demonstrable end to end.
"""
from __future__ import annotations

import re

from .config import settings
from .embeddings import embed
from .heavy import heavy_model
from .schemas import Pattern, PatternStatus

_COMPILE_SYSTEM = (
    "You write small, dependency-free, deterministic Python handlers that reproduce a "
    "narrow task an LLM was doing. Output ONLY Python source defining exactly one function "
    "`def handle(prompt: str) -> str`. No imports beyond the standard library, no network, "
    "no file or OS access. If the input does not fit the task, return the literal string "
    "'__FALLBACK__' so the router sends it to the heavy model."
)

_MOCK_HANDLER = '''\
def handle(prompt: str) -> str:
    """Deterministic sentiment classifier compiled from recurring traffic."""
    text = prompt.lower()
    positive = ("love", "great", "good", "happy", "excellent", "amazing", "awesome")
    negative = ("hate", "bad", "terrible", "awful", "worst", "angry", "broken")
    score = sum(w in text for w in positive) - sum(w in text for w in negative)
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "__FALLBACK__"
'''


def _extract_code(text: str) -> str:
    fence = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (fence.group(1) if fence else text).strip()


def compile_handler(name: str, description: str, examples: list[str]) -> Pattern:
    """Synthesize a draft handler for a described pattern."""
    centroid = _centroid(examples) if examples else embed(description)

    if settings.heavy_backend == "mock":
        code = _MOCK_HANDLER
    else:
        prompt = (
            f"Task: {description}\n\nExample prompts this handler must cover:\n"
            + "\n".join(f"- {e}" for e in examples[:10])
            + "\n\nWrite the handler now."
        )
        code = _extract_code(heavy_model().generate(prompt, system=_COMPILE_SYSTEM))

    return Pattern(
        name=name,
        description=description,
        template=description,
        embedding=centroid,
        handler_code=code,
        status=PatternStatus.draft,
    )


def _centroid(texts: list[str]) -> list[float]:
    import numpy as np

    vecs = [np.asarray(embed(t), dtype="float32") for t in texts]
    mean = np.mean(vecs, axis=0)
    norm = np.linalg.norm(mean)
    if norm > 0:
        mean = mean / norm
    return mean.tolist()
