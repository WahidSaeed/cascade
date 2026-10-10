"""Stages 2 and 4 of the light layer: the exact cache and the semantic cache (BUILD.md §4.1).

Both are persisted through the database (`cache_entries`) and the semantic index is rebuilt
from it at startup. Scope: per user by default; `global` entries are shared and only written
for prompts that pass PII screening (rule 6). Knowledge-scoped entries carry the context
fingerprint, and a hit requires the same fingerprint (rule 13).

Phase 7: on Postgres the semantic lookup queries pgvector (HNSW on `cache_entries.embedding`, see
light/pgindex.py), so every API task shares one index; the in-memory hnswlib index and the in-process
exact-key set stay the SQLite / quick-start path.

A semantic hit also requires equal slots (rule 12): "convert 5 km" never returns the answer
cached for "convert 50 km". (The template *text* may differ, otherwise a reworded prompt could
never hit; see docs/decisions.md.)
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, or_, select

from .. import db
from ..config import settings
from ..models import CacheEntry
from ..state import on_reset
from . import pgindex
from .hotcache import hot_cache
from .index import VectorIndex
from .normalize import Normalized

GLOBAL = "global"


@dataclass
class CachedAnswer:
    id: str
    scope: str
    user_id: str
    answer: str
    prompt: str
    slots: dict[str, Any]
    usage: dict[str, Any]
    model_id: str
    context_fingerprint: str | None
    sources: list[Any] = field(default_factory=list)
    similarity: float = 1.0


def _to_answer(row: CacheEntry, similarity: float = 1.0) -> CachedAnswer:
    return CachedAnswer(id=row.id, scope=row.scope, user_id=row.user_id, answer=row.answer, prompt=row.prompt,
                        slots=dict(row.slots or {}), usage=dict(row.usage or {}), model_id=row.model_id,
                        context_fingerprint=row.context_fingerprint, sources=list(row.sources or []),
                        similarity=similarity)


def scope_name(scope: str, user_id: str) -> str:
    return GLOBAL if scope == GLOBAL else f"user:{user_id}"


class LightCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._indexes: dict[str, VectorIndex] = {}
        self._exact: dict[str, float] = {}     # scoped exact_key -> latest expiry (misses never touch the DB)
        # entry id -> (scope, user_id, fingerprint, slots, expires_at, template): semantic near-misses never
        # touch the DB
        self._meta: dict[str, tuple[str, str, str | None, dict[str, Any], float, str]] = {}
        self._loaded = False

    # ------------------------------------------------------------------ index management
    def _index(self, name: str) -> VectorIndex:
        if name not in self._indexes:
            self._indexes[name] = VectorIndex(dim=settings.embed_dim)
        return self._indexes[name]

    @staticmethod
    def shared_index() -> bool:
        """True when lookups go to the database's pgvector index instead of this process's memory."""
        return settings.pgvector_index and db.dialect_name() == "postgresql"

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            if self.shared_index():
                self._loaded = True
                return
            now = time.time()
            with db.session() as s:
                rows = s.execute(select(CacheEntry.id, CacheEntry.scope, CacheEntry.user_id, CacheEntry.embedding,
                                        CacheEntry.exact_key, CacheEntry.expires_at, CacheEntry.context_fingerprint,
                                        CacheEntry.slots, CacheEntry.template)
                                 .where(CacheEntry.expires_at > now)).all()
            for entry_id, scope, user_id, emb, key, exp, fp, slots, tmpl in rows:
                self._exact[key] = max(exp, self._exact.get(key, 0.0))
                self._meta[entry_id] = (scope, user_id, fp, dict(slots or {}), exp, tmpl)
                if emb:
                    self._index(scope_name(scope, user_id)).add(entry_id, emb)
            self._loaded = True

    def reset(self) -> None:
        hot = hot_cache()
        if hot is not None:
            hot.flush()
        with self._lock:
            self._indexes = {}
            self._exact = {}
            self._meta = {}
            self._loaded = False

    # ------------------------------------------------------------------ lookups
    def lookup_exact(self, norm: Normalized, user_id: str, fingerprint: str | None) -> CachedAnswer | None:
        self.ensure_loaded()
        now = time.time()
        keys = {norm.scoped_key(f"user:{user_id}", fingerprint), norm.scoped_key(GLOBAL, fingerprint)}
        if not self.shared_index():     # another API task may have written the key: only skip the DB locally
            keys = {k for k in keys if self._exact.get(k, 0.0) > now}
        if not keys:
            return None
        hot = hot_cache()
        ordered = sorted(keys, key=lambda k: k != norm.scoped_key(f"user:{user_id}", fingerprint))  # own first
        if hot is not None:
            for entry in hot.get_many(ordered):
                if entry and entry["expires_at"] > now and entry["context_fingerprint"] == fingerprint \
                        and (entry["scope"] == GLOBAL or entry["user_id"] == user_id):
                    return CachedAnswer(**{k: v for k, v in entry.items() if k != "expires_at"})
        with db.lookup() as s:
            rows = s.scalars(select(CacheEntry).where(
                CacheEntry.exact_key.in_(keys), CacheEntry.expires_at > now,
                or_(CacheEntry.scope == GLOBAL, CacheEntry.user_id == user_id))).all()
            rows = [r for r in rows if r.context_fingerprint == fingerprint]
            if not rows:
                return None
            row = sorted(rows, key=lambda r: (r.scope == GLOBAL, -r.created_at))[0]
            answer = _to_answer(row)
            if hot is not None:
                hot.put(row.exact_key, {**asdict(answer), "expires_at": row.expires_at}, row.expires_at)
            return answer

    def lookup_semantic(self, norm: Normalized, vector: list[float], user_id: str,
                        fingerprint: str | None) -> CachedAnswer | None:
        self.ensure_loaded()
        threshold = settings.cache_threshold
        if self.shared_index():
            # equal slots are filtered in SQL, so entries for other values never crowd out the right one
            for c in pgindex.semantic_candidates(vector, user_id, fingerprint, threshold, slots=norm.slots):
                if not _slots_equal(c["slots"], norm.slots) or _reordered(c["template"], norm.template):
                    continue
                return CachedAnswer(id=c["id"], scope=c["scope"], user_id=c["user_id"], answer=c["answer"],
                                    prompt=c["prompt"], slots=c["slots"], usage=dict(c["usage"] or {}),
                                    model_id=c["model_id"], context_fingerprint=c["context_fingerprint"],
                                    sources=list(c["sources"] or []), similarity=c["sim"])
            return None
        candidates: list[tuple[str, float]] = []
        for name in (f"user:{user_id}", GLOBAL):
            idx = self._indexes.get(name)
            if idx is not None:
                candidates += [(k, sim) for k, sim in idx.search(vector, k=32) if sim >= threshold]
        if not candidates:
            return None
        candidates.sort(key=lambda c: -c[1])
        now = time.time()
        for key, sim in candidates:
            meta = self._meta.get(key)
            if meta is None:
                continue
            scope, owner, fp, slots, expires, tmpl = meta
            if expires <= now or (scope != GLOBAL and owner != user_id) or fp != fingerprint:
                continue
            if not _slots_equal(slots, norm.slots) or _reordered(tmpl, norm.template):
                continue
            with db.session() as s:
                row = s.get(CacheEntry, key)
                if row is not None and row.expires_at > now:
                    return _to_answer(row, sim)
        return None

    # ------------------------------------------------------------------ writes
    def put(self, norm: Normalized, prompt: str, vector: list[float] | None, answer: str, user_id: str,
            scope: str, usage: dict[str, Any], model_id: str, fingerprint: str | None = None,
            sources: list[Any] | None = None, ttl_s: int | None = None, now: float | None = None,
            pattern_id: str | None = None) -> str | None:
        if norm.context_dependent:
            return None
        ttl = ttl_s if ttl_s is not None else settings.cache_ttl_s
        if norm.volatile:
            if settings.volatile_ttl_s <= 0:
                return None
            ttl = min(ttl, settings.volatile_ttl_s)
        t = now or time.time()
        scope = GLOBAL if scope == GLOBAL else "user"
        entry = CacheEntry(scope=scope, user_id=user_id, exact_key=norm.scoped_key(scope_name(scope, user_id),
                                                                                    fingerprint),
                           template_key=norm.template_key, template=norm.template, slots=norm.slots, prompt=prompt,
                           answer=answer, embedding=vector, usage=usage, model_id=model_id,
                           context_fingerprint=fingerprint, sources=sources or [], created_at=t,
                           expires_at=t + ttl, hits=0, pattern_id=pattern_id)
        with db.session() as s:
            s.add(entry)
            s.flush()
            entry_id = entry.id
        self.ensure_loaded()
        if self.shared_index():
            return entry_id
        with self._lock:
            self._exact[entry.exact_key] = max(entry.expires_at, self._exact.get(entry.exact_key, 0.0))
            self._meta[entry_id] = (scope, user_id, fingerprint, dict(norm.slots), entry.expires_at, norm.template)
            if vector:
                self._index(scope_name(scope, user_id)).add(entry_id, vector)
        return entry_id


    def invalidate_pattern(self, pattern_id: str) -> int:
        """Drop every cached answer an artifact produced (on edit / reject / demote, rule 3)."""
        with db.session() as s:
            rows = s.execute(select(CacheEntry.id, CacheEntry.scope, CacheEntry.user_id, CacheEntry.exact_key)
                             .where(CacheEntry.pattern_id == pattern_id)).all()
            s.execute(delete(CacheEntry).where(CacheEntry.pattern_id == pattern_id))
        hot = hot_cache()
        if hot is not None:
            hot.delete(sorted({key for _, _, _, key in rows}))
        with self._lock:
            for entry_id, scope, user_id, key in rows:
                self._meta.pop(entry_id, None)
                self._exact.pop(key, None)
                idx = self._indexes.get(scope_name(scope, user_id))
                if idx is not None:
                    idx.remove(entry_id)
        return len(rows)


def _reordered(a: str, b: str) -> bool:
    """Same words in a different order ("translate English to French" / "French to English") change the
    meaning, so such templates never share a semantic hit."""
    return a != b and sorted(a.split()) == sorted(b.split())


def _slots_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return {k: list(v) for k, v in a.items()} == {k: list(v) for k, v in b.items()}


_cache = LightCache()


def light_cache() -> LightCache:
    return _cache


on_reset(_cache.reset)
