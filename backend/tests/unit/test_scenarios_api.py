"""Demo scenarios S1 and S2 at API level (BUILD.md §16.3 Phase 1; browser versions in web/e2e/demo)."""
import pytest

from app.seed import seed_users
from tests.helpers import install

EXTRACT = "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'"


@pytest.fixture
def demo(client):
    seed_users()
    install("extraction")
    install("conversion")
    return client


def ask(c, prompt, user="ava", conv=None):
    r = c.post("/api/chat", json={"prompt": prompt, "conversation_id": conv}, headers={"X-User-Id": user})
    assert r.status_code == 200, r.text
    return r.json()


def test_s1_live_routing(demo):
    a = ask(demo, EXTRACT)
    assert a["route"] == "artifact" and a["tokens"]["tokens_avoided"] > 0 and a["latency_ms"] < 500
    assert '"order_number": "NP-48213"' in a["answer"] and a["light"]["stage_hit"] == "recognizer"
    b = ask(demo, "Draft a friendly reply to a customer whose tent pole broke")
    assert b["route"] == "llm" and b["tokens"]["input_tokens"] > 0 and b["tokens"]["cost_actual_usd"] > 0
    c = ask(demo, EXTRACT)
    assert c["route"] == "cache" and c["answer"] == a["answer"] and c["light"]["stage_hit"] == "exact_cache"
    assert c["tokens"]["estimate_method"] == "pattern_measured"     # an artifact's answer: honest label
    for r in (a, b, c):
        assert r["light"]["reason"]                                  # the "why" panel


def test_s2_safety_rails(demo):
    five = ask(demo, "Convert 5 km to miles")
    fifty = ask(demo, "Convert 50 km to miles")
    assert fifty["route"] != "cache" and fifty["answer"] == "50 km = 31.07 miles"
    assert five["answer"] == "5 km = 3.11 miles"
    t1 = ask(demo, "What's the status of today's shipments?")
    t2 = ask(demo, "What's the status of today's shipments?")
    assert t1["light"]["volatile"] and t2["route"] == "llm"
    first = ask(demo, "How many vacation days in France?", user="dana")
    fu = ask(demo, "and for Spain?", user="dana", conv=first["conversation_id"])
    assert fu["light"]["context_dependent"] and fu["light"]["stage_hit"] == "skipped"
    assert fu["route"] == "llm" and fu["tokens"]["history_tokens"] > 0
