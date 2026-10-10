"""The knowledge step of the router (BUILD.md §4.5, §9.3, rule 13).

prepare_knowledge  runs BEFORE the caches for knowledge-scoped prompts: access-filtered candidate
                   retrieval (reusing the light layer's embedding, so it costs no tokens) reduced to a
                   context fingerprint. Both cache keys include the fingerprint.
build_context      runs on the LLM path only: cross-encoder rerank, relevance gate (`rag_rerank_min`),
                   top-k and token budget, adjacent chunks merged, near-duplicates dropped.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from ..config import settings
from ..heavy import ContextBlock, count_tokens
from ..light.embedder import get_embedder
from ..state import on_reset
from ..store import store
from .rerank import rerank
from .retriever import Candidate, fingerprint, get_retriever, relevant

if TYPE_CHECKING:
    from ..engine import Ctx, Knowledge

EMPTY_FINGERPRINT = hashlib.sha256(b"no-candidates").hexdigest()

_lock = threading.Lock()
_retrieval_ms: deque[float] = deque(maxlen=2000)


def prepare_knowledge(ctx: Ctx, collection_ids: list[str]) -> Knowledge:
    from ..engine import Knowledge

    t = time.perf_counter()
    principals = store().principals(ctx.user_id)
    query = ctx.prompt
    if ctx.norm.context_dependent:
        prev = next((h["text"] for h in reversed(ctx.history) if h["role"] == "user"), "")
        query = f"{prev} {ctx.prompt}".strip()
    emb = get_embedder()
    vec, tokens = emb.embed(query)
    if query == ctx.prompt:                      # the same vector serves the light layer: no second embed
        ctx.vector = vec
        ctx.embed_tokens += tokens if emb.paid else 0
    elif emb.paid:
        ctx.embed_tokens += tokens
    cands = relevant(get_retriever().candidates(vec, query, principals, collection_ids, settings.rag_candidates))
    fp = fingerprint(cands) if cands else EMPTY_FINGERPRINT
    ms = (time.perf_counter() - t) * 1000
    ctx.stages["retrieve"] = round(ms, 3)
    with _lock:
        _retrieval_ms.append(ms)
    return Knowledge(collection_ids=list(collection_ids), principals=principals, candidates=cands, fingerprint=fp,
                     retrieval_query=query)


def select_context(cands: list[Candidate], query: str) -> list[tuple[Candidate, float]]:
    """Rerank and gate: the chunks worth sending, best first, within the top-k and token budget."""
    if not cands:
        return []
    scores = rerank(query, [f"{c.heading_path}\n{c.text}" for c in cands])
    ranked = sorted(zip(cands, scores, strict=True), key=lambda cs: -cs[1])
    chosen: list[tuple[Candidate, float]] = []
    budget = settings.rag_max_context_tokens
    seen: set[str] = set()
    for c, s in ranked:
        if s < settings.rag_rerank_min or len(chosen) >= settings.rag_top_k:
            break
        key = " ".join(c.text.lower().split())[:300]
        if key in seen:                          # near-duplicate
            continue
        # budget on what is actually sent: the rendered block ("[n] title, p. X" + text) and its separator
        cost = count_tokens(ContextBlock(n=len(chosen) + 1, title=c.title, text=c.text, page=c.page).render()) + 1
        if cost > budget:
            continue
        seen.add(key)
        chosen.append((c, s))
        budget -= cost
    return chosen


def build_context(knowledge: Knowledge, question: str) -> list[ContextBlock]:
    chosen = select_context(list(knowledge.candidates), knowledge.retrieval_query or question)
    if not chosen:
        return []
    # merge adjacent chunks of the same document (kept in reading order), numbered by relevance
    by_doc: dict[str, list[Candidate]] = {}
    order: list[str] = []
    for c, _ in chosen:
        if c.document_id not in by_doc:
            order.append(c.document_id)
        by_doc.setdefault(c.document_id, []).append(c)
    blocks: list[ContextBlock] = []
    for doc_id in order:
        parts = sorted(by_doc[doc_id], key=lambda c: c.ord)
        groups: list[list[Candidate]] = [[parts[0]]]
        for c in parts[1:]:
            if c.ord == groups[-1][-1].ord + 1:
                groups[-1].append(c)
            else:
                groups.append([c])
        for g in groups:
            first = g[0]
            blocks.append(ContextBlock(n=len(blocks) + 1, title=first.title, text="\n".join(c.text for c in g),
                                       chunk_id=first.chunk_id, document_id=first.document_id, page=first.page,
                                       collection_id=first.collection_id, version=first.doc_version))
    return blocks


def search(query: str, user_id: str, collection_ids: list[str], k: int | None = None) -> dict[str, Any]:
    """POST /api/search: retrieval only, for debugging (candidates, scores, fingerprint, context)."""
    principals = store().principals(user_id)
    vec, _ = get_embedder().embed(query)
    t = time.perf_counter()
    cands = get_retriever().candidates(vec, query, principals, collection_ids, k or settings.rag_candidates)
    ms = (time.perf_counter() - t) * 1000
    rel = relevant(cands)
    chosen = select_context(rel, query)
    rerank_scores = {c.chunk_id: s for c, s in chosen}
    from ..engine import Knowledge

    blocks = build_context(Knowledge(collection_ids, principals, rel, None, query), query)
    context_text = "\n\n".join(b.render() for b in blocks)
    return {
        "query": query, "principals": principals, "retrieval_ms": round(ms, 2),
        "fingerprint": fingerprint(rel) if rel else EMPTY_FINGERPRINT,
        "relevant": [c.chunk_id for c in rel],
        "candidates": [{"chunk_id": c.chunk_id, "document_id": c.document_id, "title": c.title,
                        "collection_id": c.collection_id, "version": c.doc_version, "score": round(c.score, 5),
                        "similarity": round(c.similarity, 4),
                        "vector_rank": c.vector_rank, "text_rank": c.text_rank,
                        "rerank": round(rerank_scores[c.chunk_id], 3) if c.chunk_id in rerank_scores else None,
                        "snippet": c.text[:200]} for c in cands],
        "context": [{"n": b.n, "title": b.title, "chunk_id": b.chunk_id, "text": b.text} for b in blocks],
        "context_tokens": count_tokens(context_text),
    }


def retrieval_latency() -> dict[str, float]:
    with _lock:
        v = sorted(_retrieval_ms)
    if not v:
        return {"p50": 0.0, "p95": 0.0, "n": 0}
    return {"p50": round(v[len(v) // 2], 2), "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))], 2), "n": len(v)}


on_reset(lambda: _retrieval_ms.clear())
