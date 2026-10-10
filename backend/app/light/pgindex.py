"""Phase 7 (BUILD.md §13): the semantic cache and the pattern kNN in pgvector, so every API task shares one
index. The in-memory hnswlib index stays the SQLite / quick-start path.

Both queries filter in SQL (scope or owner, expiry, context fingerprint; approved patterns only) and use the
HNSW indexes with iterative scans, so a user who owns few entries still gets a full candidate list.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from sqlalchemy import text

from .. import db


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


PATTERN_VECTORS_LOCK = 7_201_337          # pg advisory lock id for the pattern_vectors rewrite
COLUMNS = ("id", "scope", "user_id", "answer", "prompt", "slots", "usage", "model_id", "context_fingerprint",
           "sources", "template")


_PATTERN_SCORES_SQL = ("SELECT DISTINCT ON (pattern_id) pattern_id, kind, 1 - (embedding <=> CAST(:q AS vector)) AS sim "
                       "FROM pattern_vectors ORDER BY pattern_id, embedding <=> CAST(:q AS vector), kind")
_prefetched = threading.local()


def semantic_candidates(vector: list[float], user_id: str, fingerprint: str | None, threshold: float,
                        k: int = 8, slots: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Nearest cache entries visible to `user_id` with the same fingerprint and equal slots, cosine >= threshold
    — each with its full answer. The same round trip also scores the vector against every approved pattern
    (a semantic miss goes to the recognizer next, which then needs no query of its own). hnsw.iterative_scan /
    ef_search are set per connection (db.make_engine)."""
    sql = text(
        "WITH sem AS ("
        f"SELECT {', '.join(COLUMNS)}, 1 - (embedding <=> CAST(:q AS vector)) AS sim "
        "FROM cache_entries "
        "WHERE expires_at > :now AND embedding IS NOT NULL "
        "AND (scope = 'global' OR user_id = :u) "
        "AND context_fingerprint IS NOT DISTINCT FROM :fp "
        "AND (CAST(:slots AS jsonb) IS NULL OR CAST(slots AS jsonb) = CAST(:slots AS jsonb)) "
        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k), "
        f"pat AS ({_PATTERN_SCORES_SQL}) "
        "SELECT 'c' AS t, to_jsonb(sem) AS j FROM sem UNION ALL SELECT 'p', to_jsonb(pat) FROM pat")
    with db.lookup() as c:
        rows = c.execute(sql, {"q": _vec(vector), "now": time.time(), "u": user_id, "fp": fingerprint, "k": k,
                                "slots": None if slots is None else json.dumps(
                                    {key: list(v) for key, v in slots.items()})}).all()
    out, scores = [], {}
    for t, j in rows:
        if t == "p":
            scores[j["pattern_id"]] = (float(j["sim"]), "centroid" if j["kind"] == "centroid" else "knn")
            continue
        d = {c: j[c] for c in COLUMNS}
        d["sim"] = float(j["sim"])
        if d["sim"] >= threshold:
            d["slots"] = dict(d["slots"] or {})
            out.append(d)
    out.sort(key=lambda d: -d["sim"])
    _prefetched.vector, _prefetched.scores = vector, scores
    return out


def sync_pattern_vectors(_patterns: list[Any] | None = None) -> None:
    """Rewrite `pattern_vectors` from the approved patterns (on approve / demote / retire, and at task start).
    Serialized across API tasks with an advisory lock, and the approved set is re-read inside the lock, so a
    task holding a stale list can never overwrite a newer one."""
    from ..store import store

    with db.engine().begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": PATTERN_VECTORS_LOCK})
        patterns = store().approved_patterns()
        c.execute(text("DELETE FROM pattern_vectors"))
        for p in patterns:
            rows = ([{"p": p.id, "kind": "centroid", "e": _vec(p.centroid)}] if p.centroid else []) + \
                   [{"p": p.id, "kind": "exemplar", "e": _vec(e)} for e in p.exemplars]
            if rows:
                c.execute(text("INSERT INTO pattern_vectors (pattern_id, kind, embedding) "
                               "VALUES (:p, :kind, CAST(:e AS vector))"), rows)


def pattern_scores(vector: list[float]) -> dict[str, tuple[float, str]]:
    """Best cosine per approved pattern over its centroid and exemplars: {pattern_id: (score, 'centroid'|'knn')}.
    Exact per pattern (DISTINCT ON), so the runner-up — and therefore the margin gate — is never hidden. Reuses
    the scores fetched with this request's semantic-cache query when the vector is the same object."""
    if getattr(_prefetched, "vector", None) is vector:
        scores: dict[str, tuple[float, str]] = _prefetched.scores
        _prefetched.vector = None
        return scores
    with db.lookup() as c:
        rows = c.execute(text(_PATTERN_SCORES_SQL), {"q": _vec(vector)}).all()
    return {pid: (float(sim), "centroid" if kind == "centroid" else "knn") for pid, kind, sim in rows}
