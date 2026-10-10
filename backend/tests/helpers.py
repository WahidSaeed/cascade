"""Test helpers: install demo artifacts through the real compile -> verify -> approve gate."""
from __future__ import annotations

import random

from app import demo_world as w
from app import patterns as pat
from app.schemas import CompileRequest, Pattern

MAKERS = {
    "extraction": ("Order details extraction", "extraction", w.make_extraction),
    "conversion": ("Unit conversion", "transform", w.make_conversion),
}


def examples(task: str, n: int = 30, seed: int = 1) -> list[str]:
    rng = random.Random(seed)
    return [MAKERS[task][2](rng) for _ in range(n)]


def install(task: str, approver: str = "eli") -> Pattern:
    name, ttype, _ = MAKERS[task]
    p = pat.compile_pattern(CompileRequest(name=name, description=name, task_type=ttype, examples=examples(task)))
    res = pat.verify_pattern(p.id)
    assert res.passed, res
    return pat.approve(p.id, approver)
