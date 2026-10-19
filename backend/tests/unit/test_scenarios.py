"""The ml / dl scenarios (app/demo_scenarios.py) and `seed --extend`: labels a reader would give, enough distinct
rows to train on, and installs through the real agent -> gate -> approve path without wiping existing data."""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from app import demo_scenarios as sc
from app import demo_world as w
from app import seed
from app.config import settings
from app.engine import route
from app.schemas import PatternStatus
from app.store import store

DATA = Path(__file__).resolve().parents[3] / "demo" / "data"


@pytest.mark.parametrize("s", sc.SCENARIOS, ids=lambda s: s.key)
def test_every_generated_label_is_the_oracle_label(s):
    rng = random.Random(s.key)
    for _ in range(500):
        prompt, label = s.make(rng)
        assert sc.oracle(prompt) == label and w.oracle(prompt) == label, prompt
        assert w.detect_task(prompt) is None                    # never mistaken for an existing task


@pytest.mark.parametrize("s", sc.SCENARIOS, ids=lambda s: s.key)
def test_training_sets_are_balanced_and_large_enough(s):
    rows = [json.loads(line) for line in (DATA / "scenarios" / f"{s.key}_train.jsonl").read_text().splitlines()]
    texts = {w.quoted(r["prompt"]).lower() for r in rows}
    assert len(texts) == len(rows)                              # datasets are de-duplicated on the payload
    counts = Counter(r["label"] for r in rows)
    assert set(counts) == set(s.labels) and max(counts.values()) - min(counts.values()) <= 1
    need = settings.dl_min_samples if s.kind == "dl" else settings.ml_min_samples
    assert len(rows) - len(rows) // 4 >= need                   # a quarter is held out for the user-label check


def test_extra_week_tests_token_saving():
    rows = [json.loads(line) for line in (DATA / "traffic_extra.jsonl").read_text().splitlines()]
    tests = Counter(r["test"] for r in rows)
    assert tests["exact_repeat"] >= 300 and tests["paraphrase"] >= 100 and tests["artifact"] >= 1000
    assert {r["task"] for r in rows} >= {s.key for s in sc.SCENARIOS}
    assert all(r["truth"] == w.oracle(r["prompt"]) for r in rows if r["test"] == "artifact")


@pytest.mark.parametrize("key", ["catalog", "return_reason"])
def test_a_scenario_installs_and_serves(fresh_db, key):
    """One ml and one dl scenario through the real agent run, the gate and approval; then traffic is served by it."""
    seed.seed_users()
    pid = seed.install_scenario(key, 0.0)
    p = store().get_pattern(pid)
    assert p is not None and p.status == PatternStatus.approved and p.kind.value == sc.BY_KEY[key].kind
    prompt, label = sc.BY_KEY[key].make(random.Random(99))
    r = route(prompt, "dana")
    assert r.route == "artifact" and r.answer.strip() == label


def test_extend_adds_only_what_is_missing(fresh_db, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(seed, "install_scenario", lambda key, at: calls.append(key) or f"pat_{key}")
    monkeypatch.setattr(seed, "replay_extra", lambda week_start: 7)
    first = seed.extend()
    assert calls == [s.key for s in sc.SCENARIOS] and first["requests"] == 7
    calls.clear()
    monkeypatch.setattr(seed, "replay_extra", lambda week_start: pytest.fail("replayed twice"))
    second = seed.extend()                                      # installed patterns are not in the store (stubbed)
    assert second["requests"] == 7                              # ...but the extra week is never replayed twice
