"""Knowledge-base components: parsing, chunking, access changes, uploads, metrics, optional backends."""
import time

import boto3
import pytest
from botocore.stub import ANY, Stubber

from app import engine, seed
from app.config import settings
from app.rag import ingest
from app.rag.chunk import chunk_blocks
from app.rag.parse import Block, ParseError, detect_mime, parse
from app.rag.retriever import BedrockKBRetriever, Candidate, fingerprint, relevant, rrf
from app.store import store

ALL = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]


# ------------------------------------------------------------------ parsing / chunking
def test_parse_formats():
    html = b"<h1>Guide</h1><h2>Sizes</h2><p>Boots run small.</p><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    blocks = parse(html, "text/html")
    assert [b.kind for b in blocks] == ["heading", "heading", "para", "table"]
    assert blocks[2].heading_path == "Guide > Sizes" and blocks[3].text == "A | B\n1 | 2"
    md = b"# T\n\n## S\n\npara one\nline two\n\n| k | v |\n|---|---|\n| a | 1 |\n"
    mb = parse(md, "text/markdown")
    assert mb[2].text == "para one line two" and mb[3].text == "k | v\na | 1"
    csv_b = parse(b"country,days\nFrance,25\n", "text/csv", "Table")
    assert csv_b[0].text == "country: France; days: 25"
    assert len(parse(b"one\n\ntwo", "text/plain")) == 2
    with pytest.raises(ParseError):
        parse(b"%PDF-broken", "application/pdf")
    assert detect_mime("x.bin", b"%PDF-1.4") == "application/pdf"
    assert detect_mime("x.bin", b"PK\x03\x04").endswith("document")
    assert detect_mime("notes.txt", b"hi") == "text/plain"


def test_chunker_splits_long_sections_with_overlap():
    long = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_blocks([Block("Section", "Doc > Section", 3, "heading"), Block(long, "Doc > Section", 3)],
                          chunk_tokens=200, overlap=40)
    assert len(chunks) > 5 and all(c.token_estimate <= 260 for c in chunks)
    assert all(c.heading_path == "Doc > Section" and c.page == 3 for c in chunks)
    first_tail = chunks[0].text.split()[-10:]
    assert any(t in chunks[1].text for t in first_tail)                  # overlap carries context forward


def test_fingerprint_and_relevance():
    def c(cid, sim, v=1):
        return Candidate(cid, "d", "col", v, "t", "x", "", None, 1, 0, similarity=sim)
    cands = [c("a", 0.9), c("b", 0.86), c("z", 0.7), c("y", 0.5)]
    rel = relevant(cands)
    assert [x.chunk_id for x in rel] == ["a", "b"]
    assert fingerprint(rel) == fingerprint(list(reversed(rel)))           # order-independent
    assert fingerprint([c("a", 0.9, v=2), c("b", 0.86)]) != fingerprint(rel)  # versions matter
    assert relevant([c("q", 0.5)]) == []                                  # below the floor: nothing relevant
    assert rrf(["a", "b"], ["b", "c"])["b"] > rrf(["a", "b"], ["b", "c"])["a"]


# ------------------------------------------------------------------ collections / documents
@pytest.fixture
def kb(fresh_db):
    seed.seed_users()
    return seed.seed_knowledge()


def test_access_change_rewrites_chunk_acls(kb):
    q = "What's the bonus range for team leads?"
    assert "couldn't find" in engine.route(q, "dana", collection_ids=ALL).answer
    ingest.update_collection(seed.collection_id("compensation"), access="org")
    r = engine.route(q + " please", "dana", collection_ids=ALL)
    assert "8 to 12" in r.answer
    ingest.update_collection(seed.collection_id("compensation"), access="private")
    assert "couldn't find" in engine.route(q + " now please", "dana", collection_ids=ALL).answer
    with pytest.raises(ingest.IngestError):
        ingest.update_collection(seed.collection_id("compensation"), access="public")
    with pytest.raises(ingest.IngestError):
        ingest.create_collection("x", "ava", "group", [])


def test_delete_and_reindex(kb):
    doc = kb["returns_policy"]
    before = ingest.get_document(doc)["chunks"]
    assert ingest.reindex(doc, sync=True)["chunks"] == before
    ingest.delete_document(doc)
    r = engine.route("How quickly are refunds processed?", "ben", collection_ids=ALL)
    assert "5 business days" not in r.answer
    assert all(d["id"] != doc for d in ingest.list_documents(seed.collection_id("products")))


def test_failed_parse_marks_the_document(kb):
    with pytest.raises(ParseError):
        ingest.register(seed.collection_id("products"), "Broken", "broken.pdf", data=b"%PDF-broken", sync=True)
    d = next(x for x in ingest.list_documents(seed.collection_id("products")) if x["title"] == "Broken")
    assert d["status"] == "failed" and d["error"]


def test_upload_flow_through_the_api(kb, client):
    hdr = {"X-User-Id": "ben"}                                            # owner of Products & Returns
    up = client.post("/api/documents/upload-url", json={"collection_id": seed.collection_id("products"),
                                                        "filename": "care.md"}, headers=hdr).json()
    assert up["method"] == "PUT" and up["url"].startswith("/api/uploads")
    assert client.put(up["url"], content=b"# Care\n\n## Boots\n\nWax Summit boots twice a season.\n").status_code == 200
    assert client.put(up["url"].replace("sig=", "sig=0"), content=b"x").status_code == 403
    doc = client.post("/api/documents", json={"collection_id": seed.collection_id("products"), "title": "Care guide",
                                              "filename": "care.md", "key": up["key"]}, headers=hdr).json()
    for _ in range(100):                                                  # background ingestion
        d = client.get(f"/api/documents/{doc['id']}", headers=hdr).json()
        if d["status"] == "ready":
            break
        time.sleep(0.1)
    assert d["status"] == "ready" and d["chunks"] == 1
    chunks = client.get(f"/api/documents/{doc['id']}/chunks", headers=hdr).json()
    assert "Wax Summit boots" in chunks[0]["text"]
    # Dana may read Products & Returns but not change it
    assert client.post("/api/documents/upload-url", json={"collection_id": seed.collection_id("products"),
                                                          "filename": "x.md"}, headers={"X-User-Id": "dana"}).status_code == 403
    assert client.post("/api/documents", json={"collection_id": seed.collection_id("handbook"), "title": "t",
                                               "filename": "care.md", "key": up["key"]},
                       headers={"X-User-Id": "chen"}).status_code == 422          # key from another collection
    assert client.post(f"/api/documents/{doc['id']}/reindex", headers=hdr).status_code == 200
    assert client.delete(f"/api/documents/{doc['id']}", headers=hdr).json()["status"] == "deleted"


def test_collections_api_and_groups(kb, client):
    cols = client.get("/api/collections", headers={"X-User-Id": "ben"}).json()
    assert {c["name"] for c in cols} == {"Employee Handbook", "Products & Returns", "Support Playbook", "Compensation Guide"}
    assert all(c["documents"] >= 1 and c["tokens"] > 0 for c in cols)
    new = client.post("/api/collections", json={"name": "Mine", "access": "private"}, headers={"X-User-Id": "dana"}).json()
    assert new["owner_id"] == "dana"
    assert client.patch(f"/api/collections/{new['id']}", json={"name": "Dana's"}, headers={"X-User-Id": "ava"}).status_code == 403
    assert client.patch(f"/api/collections/{new['id']}", json={"name": "Dana's"},
                        headers={"X-User-Id": "dana"}).json()["name"] == "Dana's"
    assert client.post("/api/collections", json={"name": "x", "access": "bad"}).status_code == 422
    assert client.put("/api/users/dana/groups", json={"groups": ["support"]}, headers={"X-User-Id": "ava"}).status_code == 403
    assert client.put("/api/users/dana/groups", json={"groups": ["support"]},
                      headers={"X-User-Id": "eli"}).json()["groups"] == ["support"]
    assert client.get("/api/users/dana/groups").json()["groups"] == ["support"]
    assert seed.collection_id("playbook") in {c["id"] for c in client.get(
        "/api/collections", headers={"X-User-Id": "dana"}).json()}


def test_rag_metrics(kb, client):
    hdr = {"X-User-Id": "dana"}
    for q in ("How many weeks of parental leave do we offer?",) * 2 + ("Convert 5 km to miles",):
        client.post("/api/chat", json={"prompt": q, "collection_ids": ALL}, headers=hdr)
    m = client.get("/api/rag/metrics").json()
    assert m["knowledge_requests"] == 3 and m["knowledge_cache_hit_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["relevance_gate_rate"] == 0.5 and m["citation_rate"] == 1.0 and m["avg_context_tokens"] > 0
    assert m["retrieval_latency_ms"]["n"] >= 3


def test_search_endpoint_reports_context(kb, client):
    r = client.post("/api/search", json={"query": "Do Summit boots run small?", "collection_ids": ALL}).json()
    assert r["candidates"] and r["context"] and r["context_tokens"] > 0 and len(r["fingerprint"]) == 64
    assert r["context"][0]["title"] == "Product FAQ" and r["relevant"]


def test_bedrock_kb_retriever_uses_retrieve_with_access_filter(monkeypatch):
    client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    monkeypatch.setattr(settings, "bedrock_kb_id", "KB12345678")
    stub = Stubber(client)
    stub.add_response("retrieve", {"retrievalResults": [{"content": {"text": "Boots run small."}, "score": 0.8,
                                                          "metadata": {"chunk_id": "c1", "document_id": "d1",
                                                                       "collection_id": "col_products", "version": 2}}]},
                      {"knowledgeBaseId": "KB12345678", "retrievalQuery": {"text": "boots"}, "retrievalConfiguration": ANY})
    with stub:
        out = BedrockKBRetriever(client).candidates([0.0], "boots", ["user:dana", "org:*"], ["col_products"], 5)
    assert out[0].chunk_id == "c1" and out[0].doc_version == 2
    from app.rag.retriever import relevant

    assert relevant(out) == out                                   # KB relevance passes the gate
    assert BedrockKBRetriever(client).candidates([0.0], "boots", [], ["col_products"], 5) == []


def test_principals_from_groups(kb):
    assert store().principals("chen") == ["user:chen", "group:hr", "group:managers", "org:*"]


def test_superseded_ingestion_is_discarded(kb, monkeypatch):
    """A newer upload registered while an older one is ingesting wins; nothing is lost (§9.2)."""
    from app.rag import ingest as ing

    calls = []
    real = ing.ingest

    def fake_enqueue(document_id, sync=False):
        calls.append(document_id)

    monkeypatch.setattr(ing, "enqueue", fake_enqueue)
    col = seed.collection_id("products")
    d2 = ing.register(col, "Notes", "notes.md", data=b"# Notes\n\nVersion two text.\n")
    ing.ingest(d2["id"])                                           # v1 ready
    ing.register(col, "Notes", "notes.md", data=b"# Notes\n\nVersion three text.\n")   # pending v2
    ing.register(col, "Notes", "notes.md", data=b"# Notes\n\nVersion four text.\n")    # pending v3 supersedes
    out = real(d2["id"])
    assert out["version"] == 3 and "four" in ing.document_chunks(d2["id"])[0]["text"]
