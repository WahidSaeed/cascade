"""Phase 4 acceptance (BUILD.md §10): discovery, per-type verification, trained recognizer, drift."""
import random

import pytest
from sqlalchemy import func, select

from app import db, engine, monitor
from app import demo_world as w
from app import patterns as pat
from app.config import settings
from app.discovery import discover, notifications
from app.light.recognizer import CentroidRecognizer, ClassifierRecognizer, recognizer
from app.models import OverheadRow
from app.schemas import CompileRequest, PatternStatus, TaskType
from app.store import store
from app.verifier import agree_json, verify
from tests.helpers import install


def _burst(n_triage: int = 60, n_open: int = 40, seed: int = 3) -> None:
    rng = random.Random(seed)
    users = [u["id"] for u in w.ALL_USERS]
    for i in range(n_triage):
        cls = w.TRIAGE_CLASSES[i % len(w.TRIAGE_CLASSES)]
        engine.route(w.make_triage_prompt(rng, cls)[0], rng.choice(users))
    for i in range(n_open):
        engine.route(w.make_open_ended(rng) + f" (draft {i})", rng.choice(users))


@pytest.mark.acceptance("P4-01")
def test_discovery_ranks_a_recognized_pattern(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "discovery_min", 20)
    _burst()
    found = discover()
    assert found, "nothing discovered"
    top = found[0]
    assert top.status == PatternStatus.recognized and top.task_type == TaskType.classification
    assert top.name == "Ticket triage" and top.projected_tokens_per_day > 0 and top.projected_monthly_savings_usd > 0
    assert top.avg_input_tokens > 0 and top.frequency_per_day > 0 and top.examples
    assert all(p.task_type != "generation" for p in found)                  # open-ended work is never proposed
    assert [p.projected_tokens_per_day for p in found] == sorted((p.projected_tokens_per_day for p in found),
                                                                 reverse=True)
    note = notifications("ava")[0]
    assert note["kind"] == "pattern_recognized" and "Ticket triage" in note["title"] and "tokens/day" in note["title"]
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(OverheadRow).where(OverheadRow.category == "discovery")) > 0
    assert discover() == []                                                 # already covered: not proposed twice


@pytest.mark.acceptance("P4-02")
def test_extraction_verification_compares_json_fields(fresh_db):
    assert agree_json('{"amount": "84.90", "email": "a@example.com"}', '{"email": "A@example.com", "amount": "84.90"}') == 1.0
    assert agree_json('{"amount": "84.00", "currency": "USD", "email": "a@x.com", "order_number": "NP-1"}',
                      '{"amount": "84.90", "currency": "EUR", "email": "a@x.com", "order_number": "NP-1"}') == 0.5
    p = install("extraction")
    assert p.verification.method == "json_fields" and p.verification.score == 1.0
    # a rule that gets one field wrong is scored per field, not all-or-nothing
    bad = p.handler_code.replace('"currency": currency', '"currency": "XXX"')
    q = pat.compile_pattern(CompileRequest(name="bad", description="bad", task_type="extraction",
                                           examples=p.examples, handler_code=bad))
    res = verify(q, p.examples[:10])
    assert res.method == "json_fields" and res.score == pytest.approx(0.75)


@pytest.mark.acceptance("P4-02")
def test_generation_tasks_cannot_be_compiled(client):
    r = client.post("/api/patterns/compile", json={"name": "Replies", "description": "Draft replies",
                                                   "task_type": "generation", "examples": ["Draft a reply"]})
    assert r.status_code == 422 and "not compilable" in r.json()["detail"]
    assert client.post("/api/patterns/compile", json={"name": "x", "description": "x", "task_type": "summary",
                                                      "examples": []}).status_code == 422


@pytest.mark.acceptance("P4-03")
def test_broken_artifact_is_demoted_by_the_next_drift_run(fresh_db, client):
    p = install("extraction")
    rng = random.Random(4)
    for _ in range(15):
        assert engine.route(w.make_extraction(rng), "ava").route.value == "artifact"
    healthy = monitor.run_check(p.id)
    assert healthy[0]["agreement"] == 1.0 and not healthy[0]["demoted"]
    client.post("/api/demo/inject-faulty")                                  # silent regression in the deployed build
    assert store().get_pattern(p.id).status == PatternStatus.approved
    out = client.post("/api/demo/drift-check").json()
    report = next(r for r in out if r["pattern_id"] == p.id)
    assert report["demoted"] and report["agreement"] < settings.drift_min_agreement
    p = store().get_pattern(p.id)
    assert p.status == PatternStatus.shadow and p.alert and "Drift" in p.alert
    assert engine.route(w.make_extraction(rng), "ava").route.value == "llm"    # traffic is back on the LLM
    with db.session() as s:
        cost = s.scalar(select(func.sum(OverheadRow.cost_usd)).where(OverheadRow.category == "drift"))
    assert cost and cost > 0                                                 # drift cost is overhead
    drift = client.get(f"/api/patterns/{p.id}/drift").json()
    assert drift["samples"] >= 10 and drift["recent"][0]["agreement"] < 1.0


def test_live_drift_sampling(fresh_db, monkeypatch):
    p = install("conversion")
    monkeypatch.setattr(settings, "drift_sample_rate", 1.0)
    monkeypatch.setattr(monitor, "_pool", type("Sync", (), {"submit": staticmethod(lambda fn, *a: fn(*a))})())
    engine.route("Convert 3 km to miles", "ava")
    score, n = monitor.agreement(p.id, p.version)
    assert n == 1 and score == 1.0 and monitor.false_hit_rate() == 0.0


def test_trained_classifier_recognizer(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "recognizer_min_patterns", 3)
    install("extraction")
    install("conversion")
    assert isinstance(recognizer(), CentroidRecognizer)                     # fewer than 3 approved patterns
    # a third pattern (urgency scoring) — a rule here; Phase 5 trains the real dl model
    rng = random.Random(9)
    ex = [w.make_urgency(rng) for _ in range(40)]
    code = ("import re\nKW={'tomorrow':2,'today':2,'tonight':2,'weekend':1,'next week':1,\"haven't arrived\":1,"
            "'snapped':1,'leaking gas':2,'sizing':-1,'colour':-1,'missing':1,'asap':1,'safety':1,'no rush':-1,"
            "'just curious':-1}\n"
            "def handle(p):\n    t=p.lower()\n    s=2+sum(v for k,v in KW.items() if k in t)\n    return str(max(1,min(5,s)))\n")
    q = pat.compile_pattern(CompileRequest(name="Urgency scoring", description="u", task_type="scoring", examples=ex,
                                           handler_code=code))
    assert pat.verify_pattern(q.id).passed
    pat.approve(q.id, "eli")
    for i in range(20):
        engine.route(w.make_open_ended(rng) + f" #{i}", "ben")              # `none` traffic to learn from
    pat.edit(q.id, pat.PatternEdit(name="Urgency scoring"))                 # lifecycle change -> retrain
    from app.light.recognizer import refresh_recognizer

    refresh_recognizer()
    r = recognizer()
    assert isinstance(r, ClassifierRecognizer) and r.meta["classes"] == 4
    for prompt in (w.make_extraction(rng), w.make_conversion(rng), w.make_urgency(rng)):
        assert engine.route(prompt, "dana").route.value in ("artifact", "cache")
    assert engine.route("Write a poem about mountains at dawn", "dana").route.value == "llm"


def test_classifier_learns_patterns_from_their_earlier_llm_traffic(fresh_db, monkeypatch):
    """Review regression: prompts that went to the LLM before a pattern existed must not be taught as `none`."""
    monkeypatch.setattr(settings, "recognizer_min_patterns", 2)
    rng = random.Random(21)
    for _ in range(150):
        engine.route(w.make_extraction(rng), "ben")
        engine.route(w.make_conversion(rng), "ben")
    for i in range(60):
        engine.route(w.make_open_ended(rng) + f" #{i}", "ben")
    install("extraction")
    install("conversion")
    r = recognizer()
    assert isinstance(r, ClassifierRecognizer)
    from app.light.embedder import get_embedder
    from app.light.normalize import normalize

    fresh = [w.make_extraction(rng) for _ in range(40)] + [w.make_conversion(rng) for _ in range(40)]
    hits = 0
    for q in fresh:
        n = normalize(q)
        rec = r.recognize(n._replace(template_key="unseen") if hasattr(n, "_replace") else
                          type(n)(n.text, n.template, n.slots, n.exact_key, "unseen", n.volatile, n.context_dependent),
                          get_embedder().embed(q)[0])                   # force the classifier, not the template match
        hits += rec.pattern_id is not None
    assert hits / len(fresh) >= 0.95


def test_lookup_artifacts_are_drift_sampled(fresh_db):
    from app import seed

    seed.seed_users()
    docs = seed.seed_knowledge()
    pid = seed.install_vacation_lookup(docs["handbook_v1"])
    out = monitor.run_check(pid, n=12)
    assert out and out[0]["samples"] >= 10 and out[0]["agreement"] == 1.0 and not out[0]["demoted"]


def test_slot_only_knowledge_cluster_is_proposed_as_lookup(fresh_db, monkeypatch):
    from app import seed

    monkeypatch.setattr(settings, "discovery_min", 8)
    seed.seed_users()
    seed.seed_knowledge()
    cols = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]
    countries = list(w.VACATION_V1)
    for i, (t, c) in enumerate((t, c) for t in w.VACATION_Q for c in countries):   # 40 distinct questions
        engine.route(t.format(c=c), f"user{i % 20 + 1:02d}", collection_ids=cols)
    for q, _, _ in w.KNOWLEDGE_QUESTIONS:                      # free-form questions: left to the cache
        engine.route(q, "ben", collection_ids=cols)
    found = discover()
    lookups = [p for p in found if p.task_type == TaskType.lookup]
    assert lookups and lookups[0].name == "Vacation days by country"
    assert seed.collection_id("handbook") in lookups[0].model_card["discovery"]["collections"]
    assert all(p.task_type != "qa" for p in found)
