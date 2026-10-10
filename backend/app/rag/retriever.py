"""Retrieval (BUILD.md §9.3, rule 13): hybrid vector + full-text search, fused with reciprocal rank
fusion. The access filter (the user's principals vs chunks.acl) and the collection filter are part of
the SQL query on every backend; results are never filtered afterwards in Python.

  PgVectorRetriever   pgvector cosine top-k + Postgres full text, iterative HNSW scans (default on Postgres)
  InMemoryRetriever   SQLite quick start / tests: SQL-filtered rows, numpy cosine + BM25 in memory
  BedrockKBRetriever  optional: Bedrock Knowledge Bases `Retrieve` with metadata filters (never
                      RetrieveAndGenerate — generation and its tokens stay in Cascade's ledger)
"""
from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import text

from .. import db
from ..config import settings

RRF_K = 60
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the of to in on for and or is are do does we our i my you your how what when which who "
            "much many can be with at by it this that from as get".split())


@dataclass
class Candidate:
    chunk_id: str
    document_id: str
    collection_id: str
    doc_version: int
    title: str
    text: str
    heading_path: str
    page: int | None
    token_estimate: int
    ord: int
    score: float = 0.0                 # fused RRF score
    similarity: float = 0.0            # cosine to the query vector
    vector_rank: int | None = None
    text_rank: int | None = None


class Retriever(ABC):
    @abstractmethod
    def candidates(self, query_vec: list[float], query_text: str, principals: list[str], collection_ids: list[str],
                   k: int) -> list[Candidate]: ...


def fingerprint(cands: list[Candidate], k: int | None = None) -> str:
    """sha256 of the sorted (chunk_id, doc_version) pairs of the top-k candidates (§9.3); callers pass the
    relevant candidates (see `relevant`)."""
    top = cands[: k or settings.rag_fingerprint_k]
    pairs = sorted(f"{c.chunk_id}@{c.doc_version}" for c in top)
    return hashlib.sha256("|".join(pairs).encode()).hexdigest()


def rrf(*rankings: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for i, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + i + 1)
    return scores


def _terms(q: str) -> list[str]:
    return [w for w in _WORD.findall(q.lower()) if w not in _STOP]


_COLS = ("c.id, c.document_id, c.collection_id, c.doc_version, d.title, c.text, c.heading_path, c.page, "
         "c.token_estimate, c.ord")
_PG_COLS = _COLS + ", 1 - (c.embedding <=> CAST(:q AS vector))"


def _row(r: Any, similarity: float = 0.0) -> Candidate:
    return Candidate(chunk_id=r[0], document_id=r[1], collection_id=r[2], doc_version=int(r[3]), title=r[4],
                     text=r[5], heading_path=r[6] or "", page=r[7], token_estimate=int(r[8] or 0), ord=int(r[9]),
                     similarity=similarity)


def relevant(cands: list[Candidate], k: int | None = None) -> list[Candidate]:
    """The candidates that matter for this question: within `rag_relevance_delta` of the best match and
    above `rag_relevance_floor`. Both the context fingerprint and the LLM context use only these, so a
    cached answer never cites a chunk outside its fingerprint (rule 13)."""
    if not cands:
        return []
    best = max(c.similarity for c in cands)
    floor = max(settings.rag_relevance_floor, best - settings.rag_relevance_delta)
    keep = [c for c in cands if c.similarity >= floor]
    return sorted(keep, key=lambda c: (-c.similarity, c.chunk_id))[: k or settings.rag_fingerprint_k]


def _fuse(rows: dict[str, Candidate], vec_rank: list[str], txt_rank: list[str], k: int) -> list[Candidate]:
    fused = rrf(vec_rank, txt_rank)
    for i, cid in enumerate(vec_rank):
        rows[cid].vector_rank = i + 1
    for i, cid in enumerate(txt_rank):
        rows[cid].text_rank = i + 1
    out = []
    for cid, s in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:k]:
        rows[cid].score = s
        out.append(rows[cid])
    return out


class PgVectorRetriever(Retriever):
    def candidates(self, query_vec: list[float], query_text: str, principals: list[str], collection_ids: list[str],
                   k: int) -> list[Candidate]:
        if not collection_ids or not principals:
            return []
        vec = "[" + ",".join(f"{x:.7g}" for x in query_vec) + "]"
        terms = _terms(query_text)
        tsq = " | ".join(terms) if terms else ""
        where = ("c.acl && CAST(:principals AS text[]) AND c.collection_id = ANY(CAST(:cols AS text[])) "
                 "AND d.status = 'ready' AND d.deleted = false AND c.doc_version = d.version")
        params = {"principals": principals, "cols": collection_ids, "k": k, "q": vec, "tsq": tsq}
        with db.engine().connect() as conn:
            conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            conn.execute(text("SET LOCAL hnsw.ef_search = 100"))
            vrows = conn.execute(text(f"SELECT {_PG_COLS} FROM chunks c JOIN documents d ON d.id = c.document_id "
                                      f"WHERE {where} ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k"),
                                 params).all()
            trows = conn.execute(text(f"SELECT {_PG_COLS} FROM chunks c JOIN documents d ON d.id = c.document_id "
                                      f"WHERE {where} AND c.tsv @@ to_tsquery('english', :tsq) "
                                      f"ORDER BY ts_rank_cd(c.tsv, to_tsquery('english', :tsq)) DESC, c.id LIMIT :k"),
                                 params).all() if tsq else []
            conn.rollback()
        rows = {r[0]: _row(r, float(r[10] or 0.0)) for r in [*vrows, *trows]}
        return _fuse(rows, [r[0] for r in vrows], [r[0] for r in trows], k)


class InMemoryRetriever(Retriever):
    """SQLite / quick start. The ACL and collection filters run in SQL (json_each); vector and BM25
    scoring run in memory over the permitted rows only."""

    def candidates(self, query_vec: list[float], query_text: str, principals: list[str], collection_ids: list[str],
                   k: int) -> list[Candidate]:
        if not collection_ids or not principals:
            return []
        ph = ",".join(f":p{i}" for i in range(len(principals)))
        ch = ",".join(f":c{i}" for i in range(len(collection_ids)))
        params: dict[str, Any] = {f"p{i}": p for i, p in enumerate(principals)}
        params.update({f"c{i}": c for i, c in enumerate(collection_ids)})
        sql = (f"SELECT {_COLS}, c.embedding FROM chunks c JOIN documents d ON d.id = c.document_id "
               f"WHERE c.collection_id IN ({ch}) AND d.status = 'ready' AND d.deleted = 0 "
               f"AND c.doc_version = d.version "
               f"AND EXISTS (SELECT 1 FROM json_each(c.acl) WHERE json_each.value IN ({ph}))")
        with db.engine().connect() as conn:
            res = conn.execute(text(sql), params).all()
        if not res:
            return []
        ids = [r[0] for r in res]
        mat = np.asarray([json.loads(r[10]) if isinstance(r[10], str) else r[10] for r in res], dtype=np.float32)
        sims = mat @ np.asarray(query_vec, dtype=np.float32)
        rows = {r[0]: _row(r, float(sims[i])) for i, r in enumerate(res)}
        vec_rank = [ids[i] for i in np.argsort(-sims, kind="stable")[:k]]
        txt_rank = _bm25_rank(query_text, [(r[0], f"{r[6] or ''} {r[5]}") for r in res], k)
        return _fuse(rows, vec_rank, txt_rank, k)


def _bm25_rank(query: str, docs: list[tuple[str, str]], k: int) -> list[str]:
    from rank_bm25 import BM25Okapi

    q = _terms(query)
    if not q:
        return []
    corpus = [_WORD.findall(t.lower()) for _, t in docs]
    scores = BM25Okapi(corpus).get_scores(q)
    order = sorted(range(len(docs)), key=lambda i: (-scores[i], docs[i][0]))
    return [docs[i][0] for i in order if scores[i] > 0][:k]


class BedrockKBRetriever(Retriever):
    """Optional backend: Amazon Bedrock Knowledge Bases `Retrieve` (never RetrieveAndGenerate).
    Access is enforced by a metadata filter on the `acl` attribute written at ingestion."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-agent-runtime", region_name=settings.bedrock_region)
        self._client = client

    def candidates(self, query_vec: list[float], query_text: str, principals: list[str], collection_ids: list[str],
                   k: int) -> list[Candidate]:
        if not principals or not collection_ids:
            return []
        # the user's principals (any of) AND the requested collections, enforced by the service
        access: dict[str, Any] = {"orAll": [{"listContains": {"key": "acl", "value": p}} for p in principals]} \
            if len(principals) > 1 else {"listContains": {"key": "acl", "value": principals[0]}}
        flt: dict[str, Any] = {"andAll": [access, {"in": {"key": "collection_id", "value": collection_ids}}]}
        cfg: dict[str, Any] = {"vectorSearchConfiguration": {"numberOfResults": k, "overrideSearchType": "HYBRID",
                                                             "filter": flt}}
        resp = self._client.retrieve(knowledgeBaseId=settings.bedrock_kb_id, retrievalQuery={"text": query_text},
                                     retrievalConfiguration=cfg)
        out = []
        for i, r in enumerate(resp.get("retrievalResults", [])):
            md = r.get("metadata", {})
            out.append(Candidate(chunk_id=str(md.get("chunk_id", f"kb-{i}")), document_id=str(md.get("document_id", "")),
                                 collection_id=str(md.get("collection_id", "")), doc_version=int(md.get("version", 1)),
                                 title=str(md.get("title", "")), text=r["content"]["text"], heading_path="",
                                 page=md.get("page"), token_estimate=len(r["content"]["text"]) // 4, ord=i,
                                 score=float(r.get("score", 0.0)),
                                 # the KB's relevance score stands in for cosine in the relevance gate
                                 similarity=float(r.get("score", 0.0))))
        return out


def get_retriever() -> Retriever:
    name = settings.retriever
    if name == "auto":
        name = "pgvector" if settings.is_postgres else "memory"
    if name == "pgvector":
        return PgVectorRetriever()
    if name == "bedrock-kb":
        return BedrockKBRetriever()
    return InMemoryRetriever()
