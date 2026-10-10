"""Phase 3 acceptance (BUILD.md §9): knowledge base & RAG."""
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db, engine, seed
from app import demo_world as w
from app.config import settings
from app.heavy import NOT_FOUND
from app.light.embedder import get_embedder
from app.models import ChunkRow, LedgerRow, Notification, OverheadRow
from app.rag import ingest
from app.rag.access import readable_collection_ids
from app.rag.retriever import get_retriever
from app.rag.service import build_context, prepare_knowledge
from app.store import store

DOCS = Path(settings.demo_dir) / "docs"
ALL = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]
PARENTAL = "How many weeks of parental leave do we offer?"
BONUS = "What's the bonus range for team leads?"


@pytest.fixture
def kb(fresh_db):
    seed.seed_users()
    return seed.seed_knowledge()


def _chunks(document_id):
    with db.session() as s:
        return list(s.execute(select(ChunkRow.id, ChunkRow.doc_version).where(ChunkRow.document_id == document_id)))


@pytest.mark.acceptance("P3-01")
def test_pdf_docx_markdown_ingest_and_versions(kb):
    for key in ("compensation_guide", "handbook_v1", "product_faq", "returns_policy"):   # PDF, DOCX, MD, HTML
        d = ingest.get_document(kb[key])
        assert d["status"] == "ready" and d["chunks"] > 0 and d["version"] == 1
    v1_chunks = _chunks(kb["handbook_v1"])
    same = ingest.register(seed.collection_id("handbook"), "Employee Handbook", "handbook_v1.docx",
                           data=(DOCS / "handbook_v1.docx").read_bytes(), sync=True)
    assert same["unchanged"] and same["version"] == 1                     # idempotent on (document, sha256)
    v2 = seed.upload_handbook_v2()
    assert v2["id"] == kb["handbook_v1"] and v2["version"] == 2 and v2["status"] == "ready"
    after = _chunks(kb["handbook_v1"])
    assert after and {v for _, v in after} == {2}                         # old version's chunks are gone
    assert not ({c for c, _ in v1_chunks} & {c for c, _ in after})


@pytest.mark.acceptance("P3-02")
def test_private_collections_never_leak(kb, client):
    dana = store().principals("dana")
    readable = readable_collection_ids(dana)
    assert seed.collection_id("compensation") not in readable and seed.collection_id("playbook") not in readable
    vec, _ = get_embedder().embed(BONUS)
    cands = get_retriever().candidates(vec, BONUS, dana, ALL, 50)                 # SQL-level filter
    assert cands and all(c.collection_id in readable for c in cands)
    r = client.post("/api/search", json={"query": BONUS, "collection_ids": ALL}, headers={"X-User-Id": "dana"}).json()
    assert {c["collection_id"] for c in r["candidates"]} <= readable
    assert all("bonus" not in c["snippet"].lower() for c in r["candidates"])
    chat = client.post("/api/chat", json={"prompt": BONUS, "collection_ids": ALL}, headers={"X-User-Id": "dana"}).json()
    assert chat["answer"] == NOT_FOUND and chat["sources"] == []
    # a manager does see it
    ben = client.post("/api/search", json={"query": BONUS, "collection_ids": ALL}, headers={"X-User-Id": "ben"}).json()
    assert seed.collection_id("compensation") in {c["collection_id"] for c in ben["candidates"]}
    # listing and reading a private collection is denied
    assert seed.collection_id("compensation") not in {c["id"] for c in client.get(
        "/api/collections", headers={"X-User-Id": "dana"}).json()}
    assert client.get("/api/documents", params={"collection_id": seed.collection_id("compensation")},
                      headers={"X-User-Id": "dana"}).status_code == 404


@pytest.mark.acceptance("P3-03")
def test_citations_and_context_budget(kb, monkeypatch):
    r = engine.route(PARENTAL, "dana", collection_ids=ALL)
    assert r.route.value == "llm" and "16 weeks" in r.answer and "[1]" in r.answer
    assert r.sources and r.sources[0].title == "Employee Handbook" and r.sources[0].snippet
    row = _ledger()[-1]
    assert row.context_tokens > 0 and not row.uncited and row.sources
    # never above rag_max_context_tokens
    monkeypatch.setattr(settings, "rag_max_context_tokens", 60)
    ctx = engine.Ctx("r", "ben", "c", "Is return shipping free?", __import__("app.light.normalize", fromlist=["x"]).normalize(
        "Is return shipping free?"), [], 0.0, 0.0, False)
    k = prepare_knowledge(ctx, ALL)
    blocks = build_context(k, ctx.prompt)
    from app.heavy import count_tokens

    assert count_tokens("\n\n".join(b.render() for b in blocks)) <= 60      # what is actually sent
    # an unrelated question sends no context at all
    u = engine.route("Convert 5 km to miles", "ben", collection_ids=ALL)
    assert u.tokens.context_tokens == 0 and u.answer == "5 km = 3.11 miles"


@pytest.mark.acceptance("P3-04")
def test_shared_cache_only_with_same_sources(kb):
    first = engine.route(PARENTAL, "dana", collection_ids=ALL)
    second = engine.route(PARENTAL, "ava", collection_ids=ALL)        # Ava also reads the playbook: same sources
    assert first.route.value == "llm" and second.route.value == "cache" and second.answer == first.answer
    assert second.sources and second.light.context_fingerprint == first.light.context_fingerprint
    b1 = engine.route(BONUS, "ben", collection_ids=ALL)
    b2 = engine.route(BONUS, "chen", collection_ids=ALL)                # also a manager: same sources
    b3 = engine.route(BONUS, "dana", collection_ids=ALL)                # no access to the source: a miss
    assert b1.route.value == "llm" and b2.route.value == "cache"
    assert b3.route.value == "llm" and b3.answer == NOT_FOUND and "8 to 12" not in b3.answer


@pytest.mark.acceptance("P3-05")
def test_document_update_invalidates_by_fingerprint(kb):
    q = "How many vacation days do employees in Germany get?"
    old = engine.route(q, "dana", collection_ids=ALL)
    assert "30" in old.answer and old.sources[0].version == 1
    assert engine.route(q, "dana", collection_ids=ALL).route.value == "cache"
    seed.upload_handbook_v2()
    new = engine.route(q, "dana", collection_ids=ALL)
    assert new.route.value == "llm" and "32" in new.answer and new.sources[0].version == 2


@pytest.mark.acceptance("P3-06")
def test_lookup_artifact_reverified_and_demoted(kb):
    pid = seed.install_vacation_lookup(kb["handbook_v1"])
    p = store().get_pattern(pid)
    assert p.status.value == "approved" and p.kb_dependencies[0].version == 1 and p.task_type.value == "lookup"
    r = engine.route("How many vacation days do employees in France get?", "dana", collection_ids=ALL)
    assert r.route.value == "artifact" and r.sources and r.sources[0].title == "Employee Handbook"
    seed.upload_handbook_v2()
    p = store().get_pattern(pid)
    assert p.status.value == "shadow" and "re-verification" in (p.alert or "")
    assert any(d.prompt.endswith("Germany get?") for d in p.verification.disagreements)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(OverheadRow).where(OverheadRow.category == "reverify")) > 0
        assert s.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "reverify_alert")) == 1
    g = engine.route("How many vacation days do employees in Germany get?", "dana", collection_ids=ALL)
    assert g.route.value == "llm" and "32" in g.answer


def test_lookup_survives_a_change_that_keeps_its_facts(kb):
    pid = seed.install_vacation_lookup(kb["handbook_v1"])
    # a new, unrelated document in the dependency's collection triggers re-verification but no demotion
    ingest.register(seed.collection_id("handbook"), "Travel note", "travel.md",
                    data=b"# Travel\n\nBook trains for trips under four hours.\n", sync=True)
    assert store().get_pattern(pid).status.value == "approved"


@pytest.mark.acceptance("P3-07")
def test_ledger_token_split_and_knowledge_cache_savings(kb):
    engine.route(PARENTAL, "dana", collection_ids=ALL)
    llm = _ledger()[-1]
    assert llm.split_method == "proportional" and llm.context_tokens > 0 and llm.prompt_tokens > 0
    assert llm.system_tokens > 0 and llm.prompt_tokens + llm.context_tokens + llm.history_tokens + llm.system_tokens \
        == llm.input_tokens
    assert llm.cache_write_tokens > 0                                   # the stable prefix was cached...
    engine.route("How do I wash a down jacket?", "dana", collection_ids=ALL)
    assert _ledger()[-1].cache_read_tokens > 0                          # ...and read back on the next request
    hit = engine.route(PARENTAL, "ava", collection_ids=ALL)
    row = _ledger()[-1]
    assert hit.route.value == "cache" and row.context_avoided_tokens == llm.context_tokens
    assert row.tokens_avoided_in >= llm.context_tokens + llm.system_tokens
    from app.ledger import ledger

    s = ledger().summary()
    assert s["input_mix"]["context"] > 0 and s["context_tokens_avoided"] >= llm.context_tokens
    by_col = {c["collection_id"]: c for c in ledger().by_collection()}
    assert by_col[seed.collection_id("handbook")]["cache_hits"] >= 1


@pytest.mark.acceptance("P3-08")
def test_followup_uses_history_and_skips_cache(kb):
    first = engine.route("How many vacation days do employees in France get?", "dana", collection_ids=ALL)
    engine.route("What about Germany?", "dana", collection_ids=ALL)               # cached elsewhere
    fu = engine.route("What about Germany?", "dana", conversation_id=first.conversation_id, collection_ids=ALL)
    assert fu.light.context_dependent and fu.light.stage_hit == "skipped" and fu.route.value == "llm"
    assert "Germany" in fu.answer and "30" in fu.answer and fu.tokens.history_tokens > 0
    assert fu.sources and fu.sources[0].title == "Employee Handbook"


def _ledger():
    with db.session() as s:
        return list(s.scalars(select(LedgerRow).order_by(LedgerRow.ts, LedgerRow.request_id)))


def test_all_demo_documents_answerable(kb):
    """Every labelled knowledge question finds its fragment for a user who can read it."""
    for q, col, frag in w.KNOWLEDGE_QUESTIONS:
        user = "ben" if col != "compensation" else "chen"
        r = engine.route(q, user, collection_ids=ALL)
        assert frag.lower() in r.answer.lower(), (q, r.answer)
