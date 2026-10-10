"""Phase 1 acceptance (BUILD.md §7): light layer, users, token ledger, overhead, Demo LLM, deploy."""
import random
import time

import pytest
from sqlalchemy import func, select

from app import db, engine
from app import demo_world as w
from app.config import settings
from app.heavy import SYSTEM_PROMPT, count_tokens
from app.ledger import ledger
from app.light.embedder import FastEmbedEmbedder, get_embedder
from app.models import CacheEntry, LedgerRow, OverheadRow
from tests.helpers import install

EXTRACT = "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'"
REPLY = "Draft a friendly reply to a customer whose tent pole broke"


def _ledger_rows():
    with db.session() as s:
        return list(s.scalars(select(LedgerRow).order_by(LedgerRow.ts)))


@pytest.mark.acceptance("P1-01")
def test_light_layer_caches_with_bge_small(fresh_db):
    assert isinstance(get_embedder(), FastEmbedEmbedder)
    assert get_embedder().model_id == "BAAI/bge-small-en-v1.5" and get_embedder().dim == 384

    first = engine.route(REPLY, "ava")
    assert first.route.value == "llm"
    again = engine.route(REPLY, "ava")
    assert again.route.value == "cache" and again.light.stage_hit == "exact_cache"
    reworded = engine.route("Write a friendly reply to a customer whose tent pole broke", "ava")
    assert reworded.route.value == "cache" and reworded.light.stage_hit == "semantic_cache"
    assert reworded.answer == first.answer

    # slots differ -> never the cached answer for another amount
    a = engine.route("convert 50 USD to EUR", "ava")
    b = engine.route("convert 5 USD to EUR", "ava")
    assert a.route.value == "llm" and b.route.value == "llm"
    c5 = engine.route("Convert 5 km to miles", "ava")
    c50 = engine.route("Convert 50 km to miles", "ava")
    assert c50.route.value == "llm" and c50.answer == "50 km = 31.07 miles" and c5.answer == "5 km = 3.11 miles"

    # time-sensitive prompts are not cached
    t1 = engine.route("what's the price today", "ava")
    t2 = engine.route("what's the price today", "ava")
    assert t1.light.volatile and t2.route.value == "llm"
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(CacheEntry).where(CacheEntry.prompt == "what's the price today")) == 0


@pytest.mark.acceptance("P1-02")
def test_recognizer_matches_pattern_shapes(fresh_db):
    p = install("extraction")
    rng = random.Random(99)
    for _ in range(5):
        r = engine.route(w.make_extraction(rng), "ben")
        assert r.route.value == "artifact" and r.pattern_id == p.id and r.light.stage_hit == "recognizer"
    off = engine.route("Write a haiku about hiking boots", "ben")
    assert off.route.value == "llm" and off.pattern_id is None

    # the latency half of P1-02 runs in tests/perf (no coverage instrumentation)


@pytest.mark.acceptance("P1-03")
def test_users_have_separate_cache_entries(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "global_cache", False)
    a = engine.route(REPLY, "ava")
    b = engine.route(REPLY, "ben")
    assert a.route.value == "llm" and b.route.value == "llm"     # Ben never gets Ava's entry
    assert engine.route(REPLY, "ben").route.value == "cache"
    with db.session() as s:
        rows = list(s.scalars(select(CacheEntry)))
    assert {(r.user_id, r.scope) for r in rows} == {("ava", "user"), ("ben", "user")}

    monkeypatch.setattr(settings, "global_cache", True)
    clean = "Write a short Instagram caption for our new headlamp"
    engine.route(clean, "chen")
    shared = engine.route(clean, "dana")
    assert shared.route.value == "cache"                         # PII-free: shared globally
    pii = "Draft an apology email to omar@example.com about his late tent"
    engine.route(pii, "chen")
    assert engine.route(pii, "dana").route.value == "llm"       # contains PII: never global
    with db.session() as s:
        scope = s.scalar(select(CacheEntry.scope).where(CacheEntry.prompt == pii))
    assert scope == "user"


@pytest.mark.acceptance("P1-04")
def test_every_request_writes_one_ledger_entry(fresh_db):
    install("extraction")
    responses = [engine.route(EXTRACT, "ava"), engine.route(REPLY, "ava"), engine.route(REPLY, "ava")]
    rows = _ledger_rows()
    assert len(rows) == 3 and {r.request_id for r in rows} == {x.request_id for x in responses}
    art, llm, cache = rows
    assert art.route == "artifact" and art.input_tokens == 0 and art.tokens_avoided_in > 0 \
        and art.cost_avoided_usd > 0 and art.estimate_method == "pattern_measured" and art.runtime_cost_usd > 0
    assert llm.route == "llm" and llm.input_tokens > 0 and llm.output_tokens > 0 and llm.cost_actual_usd > 0 \
        and llm.tokens_avoided_in == 0 and llm.estimate_method is None and llm.model_id == "demo-llm"
    assert llm.prompt_tokens + llm.context_tokens + llm.history_tokens + llm.system_tokens == llm.input_tokens
    assert cache.route == "cache" and cache.input_tokens == 0 and cache.estimate_method == "cache_exact"
    assert cache.tokens_avoided_out == llm.output_tokens and cache.cost_avoided_usd > 0


def test_heuristic_estimate_without_measurement(fresh_db):
    p = install("conversion")
    from app.store import store

    p = store().get_pattern(p.id)
    p.samples_measured = 0
    store().put_pattern(p)
    engine.route("Convert 21 km to miles", "ava")
    row = _ledger_rows()[-1]
    assert row.route == "artifact" and row.estimate_method == "heuristic"
    assert row.tokens_avoided_in == count_tokens(SYSTEM_PROMPT) + count_tokens("Convert 21 km to miles") + 4


@pytest.mark.acceptance("P1-05")
def test_overhead_and_net_savings(fresh_db):
    p = install("extraction")
    with db.session() as s:
        cats = dict(s.execute(select(OverheadRow.category, func.count()).group_by(OverheadRow.category)).all())
    assert cats.get("compile") == 1 and cats.get("verify", 0) >= 20
    rng = random.Random(5)
    for _ in range(10):
        engine.route(w.make_extraction(rng), "ava")
    s = ledger().summary()
    assert s["overhead_cost_usd"] > 0 and s["gross_saved_usd"] > 0
    assert s["net_saved_usd"] == pytest.approx(s["gross_saved_usd"] - s["overhead_cost_usd"] - s["runtime_cost_usd"])
    assert s["savings_rate"] == pytest.approx(s["net_saved_usd"] / s["baseline_cost_usd"], rel=1e-4)
    assert s["baseline_cost_usd"] == pytest.approx(s["actual_cost_usd"] + s["gross_saved_usd"])
    per = {r["pattern_id"]: r for r in ledger().by_pattern()}
    assert per[p.id]["overhead_cost_usd"] > 0 and per[p.id]["requests"] == 10


@pytest.mark.acceptance("P1-06")
def test_demo_llm_token_numbers_are_realistic(fresh_db):
    r = engine.route(REPLY, "dana")
    assert r.route.value == "llm"
    # computed from the assembled prompt: system + question (+ message overhead), not a constant
    expected_in = count_tokens(SYSTEM_PROMPT) + count_tokens(REPLY) + 4
    assert r.tokens.input_tokens == expected_in
    assert r.tokens.output_tokens == count_tokens(r.answer) + settings.demo_reasoning_overhead_tokens
    long_q = "Draft a friendly reply to a customer whose tent pole broke. " + "They camped in heavy rain. " * 30
    r2 = engine.route(long_q, "dana")
    assert r2.tokens.input_tokens > r.tokens.input_tokens + 150
    assert r.tokens.cost_actual_usd == pytest.approx(
        (r.tokens.input_tokens * 3.0 + r.tokens.output_tokens * 15.0) / 1e6, rel=1e-6)


def test_demo_llm_latency_is_simulated_only_when_configured(fresh_db, monkeypatch):
    from app.heavy import DemoLLM, LLMRequest

    monkeypatch.setattr(settings, "demo_latency_ms", 30)
    t = time.perf_counter()
    res = DemoLLM().complete(LLMRequest(question=REPLY))
    assert (time.perf_counter() - t) * 1000 >= 20 and 20 <= res.latency_ms <= 40
    monkeypatch.setattr(settings, "demo_latency_ms", 0)
    assert DemoLLM().complete(LLMRequest(question=REPLY)).latency_ms == 0.0


@pytest.mark.acceptance("P1-08")
def test_followup_skips_caches(fresh_db):
    first = engine.route("How many vacation days do employees in France get?", "dana")
    conv = first.conversation_id
    # an identical earlier entry exists in the cache for another conversation...
    engine.route("what about Germany?", "dana")
    fu = engine.route("what about Germany?", "dana", conversation_id=conv)
    assert fu.light.context_dependent and fu.light.stage_hit == "skipped" and fu.route.value == "llm"
    assert fu.tokens.history_tokens > 0                   # answered with the conversation history
    row = _ledger_rows()[-1]
    assert row.history_tokens == fu.tokens.history_tokens
    with db.session() as s:
        n = s.scalar(select(func.count()).select_from(CacheEntry).where(CacheEntry.prompt == "what about Germany?"))
    assert n == 1                                          # the follow-up itself was never cached


def test_artifact_fallback_goes_to_llm(fresh_db):
    install("extraction")
    # in the pattern's shape but nothing to extract: the artifact defers
    r = engine.route("Extract the order number, email and amount: 'hello there'", "ava")
    assert r.route.value == "llm" and r.light.stage_hit == "recognizer"
    assert "fell back" in r.light.reason


def test_light_metrics_endpoint(client):
    client.post("/api/chat", json={"prompt": REPLY}, headers={"X-User-Id": "ava"})
    client.post("/api/chat", json={"prompt": REPLY}, headers={"X-User-Id": "ava"})
    m = client.get("/api/light/metrics").json()
    assert m["requests"] == 2 and m["exact_cache_hit_rate"] == 0.5
    assert "embed" in m["latency_ms"] and m["latency_ms"]["exact_cache"]["p95"] >= 0


def test_chat_endpoint_reads_user_header(client):
    r = client.post("/api/chat", json={"prompt": "Convert 3 kg to lb"}, headers={"X-User-Id": "chen"}).json()
    assert r["route"] == "llm" and r["answer"] == "3 kg = 6.61 lb"
    assert r["light"]["template"] == "convert <NUM> <UNIT> to <UNIT>"
    rows = _ledger_rows()
    assert rows[-1].user_id == "chen"
    assert client.post("/api/chat", json={"prompt": "  "}).status_code == 400
    anon = client.post("/api/chat", json={"prompt": "hello"}).json()
    assert _ledger_rows()[-1].user_id == "anonymous" and anon["route"] == "llm"
