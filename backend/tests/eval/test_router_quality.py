"""Quality thresholds (BUILD.md §14.4) on the labelled sets in demo/data/eval/.

Router: false-hit rate <= 1 % on "must not match", exactly 0 for slot mismatches, time-sensitive
prompts and follow-ups (rule-guarded); match rate >= 95 % on "must match" (for the patterns that
exist at this phase; urgency is added with its `dl` artifact).
"""
import json
from pathlib import Path

import pytest

from app import engine
from app.config import settings
from app.store import store
from tests.helpers import install

EVAL = Path(settings.demo_dir) / "data" / "eval"


def load(name):
    return [json.loads(x) for x in (EVAL / f"{name}.jsonl").read_text().splitlines() if x.strip()]


@pytest.fixture
def routed(fresh_db):
    install("extraction")
    install("conversion")
    return {p.name: p.id for p in store().approved_patterns()}


def test_router_must_match(routed):
    rows = [r for r in load("router_must_match") if r["pattern"] in routed]
    assert len(rows) >= 100
    hits = 0
    for r in rows:
        res = engine.route(r["prompt"], "eval")
        hits += res.route.value in ("artifact", "cache") and (res.pattern_id in (routed[r["pattern"]], None))
    rate = hits / len(rows)
    print(f"must-match rate {rate:.3f} over {len(rows)}")
    assert rate >= 0.95


def test_router_must_not_match(routed):
    rows = load("router_must_not_match")
    false_hits = [r for r in rows if engine.route(r["prompt"], "eval").route.value == "artifact"]
    rate = len(false_hits) / len(rows)
    print(f"false-hit rate {rate:.3f} over {len(rows)}: {false_hits}")
    assert rate <= 0.01


def test_cache_pairs_rule_guarded_cases_are_exact(routed):
    for i, pair in enumerate(load("cache_pairs")):
        user = f"pair{i}"
        engine.route(pair["first"], user)
        second = engine.route(pair["second"], user)
        hit = second.route.value == "cache"
        assert hit == pair["should_hit"], pair


def test_followups_never_hit(routed):
    for i, f in enumerate(load("followups")):
        user = f"fu{i}"
        first = engine.route(f["previous"], user)
        engine.route(f["prompt"], user)                     # cached in another conversation context
        res = engine.route(f["prompt"], user, conversation_id=first.conversation_id)
        assert res.light.context_dependent and res.route.value == "llm", f
