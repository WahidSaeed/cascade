"""Component tests: privacy, pricing, heavy model helpers, verifier comparators, runtime, ledger, API."""
import json
import os
import random
import time

import boto3
import pytest
from botocore.stub import Stubber

from app import demo_world as w
from app import engine, privacy, verifier
from app import patterns as pat
from app.config import settings
from app.heavy import NOT_FOUND, ContextBlock, DemoLLM, LLMRequest, proportional_split
from app.ledger import LedgerEntry, OverheadEntry, ledger
from app.light.cache import light_cache
from app.light.embedder import HashingEmbedder
from app.light.index import VectorIndex
from app.light.normalize import normalize
from app.pricing import cost, embed_cost, lambda_cost
from app.runtime import SubprocessRuntime, materialize
from app.schemas import ArtifactKind, CompileRequest, Pattern, TaskType
from tests.helpers import examples, install


# ------------------------------------------------------------------ privacy
@pytest.mark.parametrize("text,pii", [
    ("write to jane@example.com", True),
    ("card 4111 1111 1111 1111 please", True),
    ("call +44 20 7946 0958", True),
    ("order NP-48213 is late", True),
    ("my name is Omar Haddad", True),
    ("Draft a friendly reply to a customer whose tent pole broke", False),
    ("Convert 12 km to miles", False),
])
def test_contains_pii(text, pii):
    assert privacy.contains_pii(text) is pii


def test_card_numbers_need_luhn():
    assert "CARD" in {k for k, _ in privacy.find_pii("card 4111 1111 1111 1111")}
    assert "CARD" not in {k for k, _ in privacy.find_pii("card 4111 1111 1111 1112")}


def test_redact():
    out = privacy.redact("Order NP-48213 for jane@example.com, call +44 20 7946 0958")
    assert "jane@" not in out and "NP-48213" not in out and "<EMAIL>" in out and "<ID>" in out and "<PHONE>" in out


def test_comprehend_backend(monkeypatch):
    client = boto3.client("comprehend", region_name="us-east-1")
    stub = Stubber(client)
    stub.add_response("detect_pii_entities", {"Entities": [{"Type": "NAME", "Score": 0.99, "BeginOffset": 11,
                                                             "EndOffset": 15}]},
                      {"Text": "my name is Omar", "LanguageCode": "en"})
    monkeypatch.setattr(settings, "pii_backend", "comprehend")
    monkeypatch.setattr(privacy, "_client", client)
    with stub:
        assert privacy.find_pii("my name is Omar") == [("NAME", "Omar")]


# ------------------------------------------------------------------ pricing
def test_pricing():
    assert cost("demo-llm", 1_000_000, 0) == pytest.approx(3.0)
    assert cost("demo-llm", 0, 1_000_000) == pytest.approx(15.0)
    # cached tokens are part of the input total, billed at the cache rates
    assert cost("demo-llm", 1_000_000, 0, cache_read=1_000_000) == pytest.approx(0.30)
    assert cost("unknown-model", 1_000_000, 0) == pytest.approx(3.0)       # falls back to the heavy model
    assert embed_cost("amazon.titan-embed-text-v2:0", 1_000_000) == pytest.approx(0.02)
    assert 0 < lambda_cost(10) < lambda_cost(1000)


def test_prices_are_configurable(monkeypatch):
    monkeypatch.setattr(settings, "model_prices", json.dumps({"demo-llm": {"input": 1.0, "output": 2.0}}))
    assert cost("demo-llm", 1_000_000, 1_000_000) == pytest.approx(3.0)
    assert "_lambda" in settings.model_prices          # defaults are merged, not replaced


# ------------------------------------------------------------------ heavy model helpers
def test_proportional_split_sums_to_total():
    parts = {"system": "s" * 400, "context": "c" * 1200, "history": "", "prompt": "q" * 40}
    sp = proportional_split(431, parts)
    assert sum(sp.values()) == 431 and sp["context"] > sp["system"] > sp["prompt"] and sp["history"] == 0
    assert proportional_split(0, parts)["prompt"] == 0


def test_demo_llm_knowledge_behaviour():
    llm = DemoLLM()
    blocks = [ContextBlock(1, "Employee Handbook", "Vacation days by country\nFrance | 25\nGermany | 30"),
              ContextBlock(2, "Employee Handbook", "Northpeak offers 16 weeks of fully paid parental leave to every "
                                                   "new parent. Tell your manager at least 8 weeks before.")]
    a = llm.complete(LLMRequest("How many vacation days do employees in France get?", context=blocks, knowledge=True))
    assert a.text == "Employees in France get 25 vacation days per year [1]."
    f = llm.complete(LLMRequest("and for Germany?", context=blocks, knowledge=True,
                                history=[("user", "How many vacation days in France?"), ("assistant", "25")]))
    assert "30" in f.text and "[1]" in f.text
    q = llm.complete(LLMRequest("How many weeks of parental leave do we offer?", context=blocks, knowledge=True))
    assert "16 weeks" in q.text and "[2]" in q.text
    assert llm.complete(LLMRequest("What's the bonus range for team leads?", knowledge=True)).text == NOT_FOUND
    assert "Attach the Employee Handbook" in llm.complete(LLMRequest("vacation days in Spain?")).text
    assert llm.complete(LLMRequest("x", purpose="lookup_extract", context=blocks, meta={"slot": "Germany"})).text \
        == json.dumps({"value": "30"})
    assert "def handle" in llm.complete(LLMRequest("x", purpose="compile", meta={"task_type": "transform"})).text
    assert json.loads(llm.complete(LLMRequest("x", purpose="name_cluster", meta={"examples": [w.make_conversion(
        random.Random(1))]})).text)["task_type"] == "transform"


def test_demo_llm_prompt_cache_simulation(monkeypatch):
    llm = DemoLLM()
    blocks = [ContextBlock(1, "Doc", "text " * 50)]
    first = llm.complete(LLMRequest("q?", context=blocks, knowledge=True))
    second = llm.complete(LLMRequest("q2?", context=blocks, knowledge=True))
    assert first.cache_write_tokens > 0 and first.cache_read_tokens == 0
    assert second.cache_read_tokens == first.cache_write_tokens
    monkeypatch.setattr(settings, "prompt_caching", False)
    assert DemoLLM().complete(LLMRequest("q?", context=blocks, knowledge=True)).cache_write_tokens == 0


# ------------------------------------------------------------------ verifier comparators
def test_comparators():
    assert verifier.agree_exact("Refund.", "refund") == 1.0
    assert verifier.agree_json('{"a": 1, "b": "X"}', '{"b": "x", "a": 1}') == 1.0
    assert verifier.agree_json('{"a": 1, "b": 2}', '{"a": 1, "b": 3}') == 0.5
    assert verifier.agree_json("not json", "not json") == 1.0
    assert verifier.agree_numeric("4", "5") == 1.0 and verifier.agree_numeric("2", "5") == 0.0
    assert verifier.agree_numeric("n/a", "3") == 0.0
    assert verifier.agree_lookup("Employees in France get 25 vacation days.", '{"value": "25"}') == 1.0
    assert verifier.agree_lookup("Employees in Germany get 30 vacation days.", '{"value": "32"}') == 0.0
    assert verifier.comparator("extraction")[0] == "json_fields"
    assert verifier.threshold_for(Pattern(name="l", task_type=TaskType.lookup)) == 1.0


def test_generation_task_types_cannot_compile(fresh_db):
    with pytest.raises(pat.PatternError) as e:
        pat.compile_pattern(CompileRequest(name="x", description="x", task_type="generation", examples=["a"]))
    assert e.value.status == 422
    with pytest.raises(pat.PatternError):
        pat.compile_pattern(CompileRequest(name="l", description="l", task_type="lookup", examples=["a"]))


def test_verify_scores_against_user_labels(fresh_db):
    p = install("conversion")
    samples = examples("conversion", 6, seed=3)
    wrong = {samples[0]: "nonsense"}
    res = pat.verify_pattern(p.id, samples, expected=wrong)
    assert res.score == pytest.approx(5 / 6, abs=1e-3) and res.disagreements[0].expected == "nonsense"


# ------------------------------------------------------------------ leak check (rule 6)
def test_leak_warnings(fresh_db):
    engine.route("Extract the order number, email and amount: 'Order NP-48213 for jane@example.com cost €5'", "ava")
    code = 'def handle(prompt):\n    if "jane@example.com" in prompt:\n        return "NP-48213"\n    return "x"\n'
    warns = pat.leak_warnings(code, pat._logged_prompts())
    assert any("jane@example.com" in x for x in warns) and any("NP-48213" in x for x in warns)
    assert pat.leak_warnings("def handle(p):\n    return 'refund'\n", ["I want a refund"]) == []
    assert "does not parse" in pat.leak_warnings("def (", [])[0]


# ------------------------------------------------------------------ runtime (rule 2)
def _rule(code: str) -> Pattern:
    return Pattern(name="t", kind=ArtifactKind.rule, handler_code=code)


def test_artifact_runs_in_another_process(fresh_db):
    rt = SubprocessRuntime()
    try:
        res = rt.invoke(_rule("import os\ndef handle(p):\n    return str(os.getpid())\n"), "x")
        assert res.error is None and int(res.result) != os.getpid()
        env = rt.invoke(_rule("import os\ndef handle(p):\n    return os.environ.get('AWS_ACCESS_KEY_ID', 'none')\n"), "x")
        assert env.result == "none"                                   # scrubbed environment
    finally:
        rt.shutdown()


def test_artifact_errors_and_timeouts_fall_back(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "artifact_timeout_s", 1.0)
    rt = SubprocessRuntime()
    try:
        boom = rt.invoke(_rule("def handle(p):\n    raise ValueError('boom')\n"), "x")
        assert boom.error and "boom" in boom.error and not boom.usable
        slow = rt.invoke(_rule("import time\ndef handle(p):\n    time.sleep(5)\n    return 'late'\n"), "x")
        assert slow.error == "artifact timed out"
        broken = rt.invoke(_rule("def nothing():\n    pass\n"), "x")
        assert broken.error and not broken.usable
        low = rt.invoke(_rule("def handle(p):\n    return ('maybe', 0.2)\n"), "x")
        assert low.result == "maybe" and not low.usable
        fb = rt.invoke(_rule("def handle(p):\n    return '__FALLBACK__'\n"), "x")
        assert not fb.usable
    finally:
        rt.shutdown()


def test_materialize_requires_model_files(fresh_db):
    with pytest.raises(FileNotFoundError):
        materialize(Pattern(name="m", kind=ArtifactKind.ml, model_uri="/nonexistent"))


# ------------------------------------------------------------------ light cache & index
def test_vector_index():
    idx = VectorIndex(dim=4, capacity=2)
    for i in range(5):                                  # grows past its initial capacity
        idx.add(f"k{i}", [1.0, float(i), 0.0, 0.0])
    idx.add("k0", [0.0, 0.0, 1.0, 0.0])                 # re-adding replaces
    assert len(idx) == 5 and idx.search([0.0, 0.0, 1.0, 0.0], k=1)[0][0] == "k0"
    idx.remove("k0")
    assert "k0" not in [k for k, _ in idx.search([0.0, 0.0, 1.0, 0.0], k=5)]
    assert VectorIndex(dim=4).search([1, 0, 0, 0]) == []


def test_cache_ttl_and_rebuild(fresh_db):
    e = HashingEmbedder()
    n = normalize("Write a poem about rivers")
    vec = e.embed("Write a poem about rivers")[0]
    c = light_cache()
    c.put(n, "Write a poem about rivers", vec, "a poem", "ava", "user", {"output_tokens": 5}, "demo-llm",
          ttl_s=60, now=time.time() - 120)                                    # already expired
    assert c.lookup_exact(n, "ava", None) is None and c.lookup_semantic(n, vec, "ava", None) is None
    c.put(n, "Write a poem about rivers", vec, "fresh poem", "ava", "user", {"output_tokens": 5}, "demo-llm")
    c.reset()                                                                 # rebuilt from the database
    hit = c.lookup_semantic(n, vec, "ava", None)
    assert hit is not None and hit.answer == "fresh poem"
    assert c.lookup_semantic(n, vec, "ben", None) is None                     # user scope
    assert c.lookup_exact(n, "ava", "some-fingerprint") is None               # fingerprint must match


def test_volatile_ttl(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "volatile_ttl_s", 60)
    engine.route("What are the latest Northpeak tent models", "ava")
    assert engine.route("What are the latest Northpeak tent models", "ava").route.value == "cache"


# ------------------------------------------------------------------ ledger reconciliation
def test_ledger_aggregates_reconcile(fresh_db):
    L = ledger()
    t0 = 1_760_000_000.0
    rows = [LedgerEntry(request_id=f"r{i}", user_id=["ava", "ben"][i % 2], route=["llm", "cache", "artifact"][i % 3],
                        ts=t0 + i * 3000, pattern_id="p1" if i % 3 == 2 else None, input_tokens=100 * (i % 3 == 0),
                        output_tokens=50 * (i % 3 == 0), cost_actual_usd=0.001 * (i % 3 == 0),
                        tokens_avoided_in=80 * (i % 3 != 0), tokens_avoided_out=40 * (i % 3 != 0),
                        cost_avoided_usd=0.002 * (i % 3 != 0), runtime_cost_usd=0.00001 * (i % 3 == 2),
                        collection_ids=["colA"] if i % 4 == 0 else [],
                        estimate_method=None if i % 3 == 0 else ("cache_exact" if i % 3 == 1 else "heuristic"))
            for i in range(60)]
    L.record_many(rows)
    L.record_overhead(OverheadEntry(category="compile", input_tokens=500, output_tokens=200, cost_usd=0.01,
                                    pattern_id="p1", ts=t0 + 10, user_id="ava"))
    L.record_overhead(OverheadEntry(category="ingestion", cost_usd=0.002, collection_id="colA", ts=t0 + 20))
    s = L.summary()
    assert s["requests"] == 60 and sum(s["by_route"].values()) == 60
    assert s["gross_saved_usd"] == pytest.approx(sum(r.cost_avoided_usd for r in rows))
    assert s["overhead_cost_usd"] == pytest.approx(0.012)
    ts = L.timeseries("hour")
    assert sum(b["requests"] for b in ts) == 60
    assert sum(b["gross_saved_usd"] for b in ts) == pytest.approx(s["gross_saved_usd"])
    assert sum(b["overhead_cost_usd"] for b in ts) == pytest.approx(s["overhead_cost_usd"])
    assert sum(b["net_saved_usd"] for b in L.timeseries("day")) == pytest.approx(s["net_saved_usd"])
    users = {u["user_id"]: u for u in L.by_user()}
    assert sum(u["requests"] for u in users.values()) == 60
    assert users["ava"]["overhead_cost_usd"] == pytest.approx(0.01)
    assert L.summary(user_id="ava")["requests"] == users["ava"]["requests"]
    per = {p["pattern_id"]: p for p in L.by_pattern()}
    assert per["p1"]["requests"] == 20 and per["p1"]["overhead_cost_usd"] == pytest.approx(0.01)
    assert per["p1"]["paid_back"] and per["p1"]["paid_back_after_requests"] == 6       # 6 x (0.002 - 0.00001) >= 0.01
    assert L.summary(pattern_id="p1")["gross_saved_usd"] == pytest.approx(per["p1"]["gross_saved_usd"])
    cols = {c["collection_id"]: c for c in L.by_collection()}
    assert cols["colA"]["requests"] == 15 and cols["colA"]["overhead_cost_usd"] == pytest.approx(0.002)
    assert L.summary(collection_id="colA")["requests"] == 15
    assert L.summary(frm=t0, to=t0 + 3000)["requests"] == 1
    assert s["estimated_share"] == pytest.approx(
        sum(r.cost_avoided_usd for r in rows if r.estimate_method == "heuristic") / s["gross_saved_usd"])
    assert len(L.entries(limit=5)) == 5
    with pytest.raises(ValueError):
        L.record(LedgerEntry(request_id="bad", user_id="x", route="llm", estimate_method="guess"))


# ------------------------------------------------------------------ API
def test_pattern_and_ledger_endpoints(client):
    ex = examples("conversion")
    p = client.post("/api/patterns/compile", json={"name": "Unit conversion", "description": "convert",
                                                   "task_type": "transform", "examples": ex}).json()
    assert p["status"] == "draft" and p["kind"] == "rule"
    assert client.post(f"/api/patterns/{p['id']}/approve").status_code == 409
    v = client.post(f"/api/patterns/{p['id']}/verify", json={"samples": []}).json()
    assert v["passed"]
    a = client.post(f"/api/patterns/{p['id']}/approve", headers={"X-User-Id": "eli"}).json()
    assert a["status"] == "approved" and a["approved_by"] == "eli"
    assert client.get(f"/api/patterns/{p['id']}").json()["status"] == "approved"
    assert len(client.get("/api/patterns", params={"status": "approved"}).json()) == 1
    r = client.post("/api/chat", json={"prompt": "Convert 7 kg to lb"}).json()
    assert r["route"] == "artifact" and r["answer"] == "7 kg = 15.43 lb"
    assert client.get("/api/ledger/summary").json()["by_route"] == {"artifact": 1}
    assert client.get("/api/ledger/by-pattern").json()[0]["pattern_name"] == "Unit conversion"
    assert client.get("/api/ledger/timeseries", params={"bucket": "hour"}).status_code == 200
    assert client.get("/api/ledger/timeseries", params={"bucket": "week"}).status_code == 422
    assert client.get("/api/ledger/by-user").json()[0]["user_id"] == "anonymous"
    assert client.get("/api/ledger/by-collection").json() == []
    assert len(client.get("/api/ledger/entries").json()) == 1
    e = client.put(f"/api/patterns/{p['id']}", json={"name": "Units"}).json()
    assert e["name"] == "Units" and e["status"] == "approved"           # metadata edits keep it serving
    assert client.post(f"/api/patterns/{p['id']}/reject").json()["status"] == "rejected"
    assert client.get("/api/patterns/missing").status_code == 404
    assert client.post("/api/patterns/missing/verify", json={}).status_code == 404


def test_config_endpoint(client):
    cfg = client.get("/api/config").json()
    assert "cache_threshold" in cfg["editable"] and "database_url" not in cfg["values"]
    r = client.put("/api/config", json={"values": {"cache_threshold": 0.97, "global_cache": False}}).json()
    assert r["values"]["cache_threshold"] == 0.97 and settings.cache_threshold == 0.97
    assert client.put("/api/config", json={"values": {"database_url": "x"}}).status_code == 422
    assert client.put("/api/config", json={"values": {"cache_threshold": "high"}}).status_code == 422
    from app.store import store

    assert store().config_overrides()["cache_threshold"] == 0.97     # persisted, re-applied at startup


def test_me_and_users(client):
    from app.seed import seed_users

    seed_users()
    me = client.get("/api/me", headers={"X-User-Id": "ben"}).json()
    assert me["principals"] == ["user:ben", "group:managers", "group:support", "org:*"]
    assert client.get("/api/health").json()["db"] == "sqlite"
