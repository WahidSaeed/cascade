"""Knowledge quality thresholds (BUILD.md §14.4): retrieval recall@5 >= 0.9 on the labelled
question -> document set; every Demo LLM answer that used context cites at least one source; no
answer ever cites a chunk the user cannot read."""
import json
from pathlib import Path

import pytest

from app import demo_world as w
from app import engine, seed
from app.config import settings
from app.light.embedder import get_embedder
from app.rag.access import readable_collection_ids
from app.rag.retriever import get_retriever
from app.store import store

EVAL = Path(settings.demo_dir) / "data" / "eval"
ALL = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]
EVERYONE = ["org:*", "group:support", "group:managers", "group:hr"]


@pytest.fixture
def kb(fresh_db):
    seed.seed_users()
    return seed.seed_knowledge()


def recall_at_5(rows) -> float:
    emb = get_embedder()
    hits = 0
    for r in rows:
        vec, _ = emb.embed(r["question"])
        top = get_retriever().candidates(vec, r["question"], EVERYONE, ALL, 5)
        hits += any(c.title == r["document"] for c in top)
    return hits / len(rows)


@pytest.mark.acceptance("P7-05")
def test_retrieval_recall_at_5(kb):
    """>= 0.9 (§14.4) and never below the Phase 3 baseline (demo/data/eval/recall_baseline.json). The integration
    suite runs this same test on pgvector with the HNSW index."""
    baseline = json.loads((EVAL / "recall_baseline.json").read_text())["recall_at_5"]
    rows = [json.loads(x) for x in (EVAL / "retrieval_qa.jsonl").read_text().splitlines() if x.strip()]
    recall = recall_at_5(rows)
    print(f"retrieval recall@5 = {recall:.3f} over {len(rows)} questions (Phase 3 baseline {baseline:.3f})")
    assert recall >= 0.9
    assert recall >= baseline


def test_answers_with_context_cite_and_never_leak(kb):
    users = [p["id"] for p in w.PERSONAS]
    for user in users:
        readable = readable_collection_ids(store().principals(user))
        for q, _, _ in w.KNOWLEDGE_QUESTIONS:
            r = engine.route(q, user, collection_ids=ALL)
            if r.route.value == "llm" and r.tokens.context_tokens > 0:
                assert r.sources, (user, q, r.answer)                     # used context -> cites
            for s in r.sources:
                assert s.collection_id in readable, (user, q, s)          # never an unreadable chunk
