"""Persistence. SQLite for local dev; a DynamoDB implementation for AWS.

Everything the app needs goes through the Store interface, so swapping backends is a
config change (CASCADE_STORE_BACKEND=dynamodb) rather than a code change.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

from .config import settings
from .schemas import Pattern, PatternStatus


class Store:
    # patterns
    def list_patterns(self) -> list[Pattern]: ...
    def get_pattern(self, pattern_id: str) -> Optional[Pattern]: ...
    def put_pattern(self, pattern: Pattern) -> Pattern: ...
    def approved_patterns(self) -> list[Pattern]: ...
    # semantic cache
    def cache_lookup(self, embedding: list[float]) -> Optional[tuple[str, float]]: ...
    def cache_put(self, prompt: str, embedding: list[float], answer: str) -> None: ...
    # request log
    def log_request(self, prompt: str, embedding: list[float], route: str, latency_ms: float) -> None: ...
    def recent_logs(self, limit: int = 1000) -> list[dict]: ...
    def route_counts(self) -> dict[str, int]: ...
    def route_latency(self) -> dict[str, float]: ...


# --------------------------------------------------------------------------- SQLite
class SqliteStore(Store):
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT, embedding TEXT, answer TEXT, created_at REAL
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT, embedding TEXT, route TEXT, latency_ms REAL, created_at REAL
                );
                """
            )

    # ---- patterns ----
    def list_patterns(self) -> list[Pattern]:
        rows = self._conn.execute("SELECT data FROM patterns").fetchall()
        pats = [Pattern(**json.loads(r["data"])) for r in rows]
        return sorted(pats, key=lambda p: p.created_at, reverse=True)

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        row = self._conn.execute("SELECT data FROM patterns WHERE id=?", (pattern_id,)).fetchone()
        return Pattern(**json.loads(row["data"])) if row else None

    def put_pattern(self, pattern: Pattern) -> Pattern:
        pattern.updated_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO patterns (id, data) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (pattern.id, pattern.model_dump_json()),
            )
        return pattern

    def approved_patterns(self) -> list[Pattern]:
        return [p for p in self.list_patterns() if p.status == PatternStatus.approved]

    # ---- cache ----
    def cache_lookup(self, embedding: list[float]) -> Optional[tuple[str, float]]:
        from .embeddings import cosine

        best: Optional[tuple[str, float]] = None
        for r in self._conn.execute("SELECT answer, embedding FROM cache").fetchall():
            sim = cosine(embedding, json.loads(r["embedding"]))
            if best is None or sim > best[1]:
                best = (r["answer"], sim)
        return best

    def cache_put(self, prompt: str, embedding: list[float], answer: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache (prompt, embedding, answer, created_at) VALUES (?,?,?,?)",
                (prompt, json.dumps(embedding), answer, time.time()),
            )

    # ---- logs ----
    def log_request(self, prompt, embedding, route, latency_ms) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO logs (prompt, embedding, route, latency_ms, created_at) VALUES (?,?,?,?,?)",
                (prompt, json.dumps(embedding), route, latency_ms, time.time()),
            )

    def recent_logs(self, limit: int = 1000) -> list[dict]:
        rows = self._conn.execute(
            "SELECT prompt, embedding, route, latency_ms, created_at FROM logs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def route_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT route, COUNT(*) c FROM logs GROUP BY route"
        ).fetchall()
        return {r["route"]: r["c"] for r in rows}

    def route_latency(self) -> dict[str, float]:
        rows = self._conn.execute(
            "SELECT route, AVG(latency_ms) a FROM logs GROUP BY route"
        ).fetchall()
        return {r["route"]: round(r["a"], 2) for r in rows}


# --------------------------------------------------------------------------- DynamoDB
class DynamoStore(Store):
    """Production store. Tables are created by Terraform (see infra/)."""

    def __init__(self) -> None:
        import boto3

        self._ddb = boto3.resource("dynamodb", region_name=settings.bedrock_region)
        self._patterns = self._ddb.Table(settings.dynamo_table_patterns)
        self._cache = self._ddb.Table(settings.dynamo_table_cache)
        self._logs = self._ddb.Table(settings.dynamo_table_logs)

    def list_patterns(self) -> list[Pattern]:
        items = self._patterns.scan().get("Items", [])
        pats = [Pattern(**json.loads(i["data"])) for i in items]
        return sorted(pats, key=lambda p: p.created_at, reverse=True)

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        item = self._patterns.get_item(Key={"id": pattern_id}).get("Item")
        return Pattern(**json.loads(item["data"])) if item else None

    def put_pattern(self, pattern: Pattern) -> Pattern:
        pattern.updated_at = time.time()
        self._patterns.put_item(Item={"id": pattern.id, "data": pattern.model_dump_json()})
        return pattern

    def approved_patterns(self) -> list[Pattern]:
        return [p for p in self.list_patterns() if p.status == PatternStatus.approved]

    # For brevity the cache/log methods below use scan; in production add a GSI or
    # push cache lookups into a vector store (OpenSearch/pgvector) — see ARCHITECTURE.md.
    def cache_lookup(self, embedding):  # pragma: no cover - prod path
        from .embeddings import cosine

        best = None
        for i in self._cache.scan().get("Items", []):
            sim = cosine(embedding, json.loads(i["embedding"]))
            if best is None or sim > best[1]:
                best = (i["answer"], sim)
        return best

    def cache_put(self, prompt, embedding, answer):  # pragma: no cover
        import uuid

        self._cache.put_item(Item={
            "id": uuid.uuid4().hex, "prompt": prompt,
            "embedding": json.dumps(embedding), "answer": answer, "created_at": str(time.time()),
        })

    def log_request(self, prompt, embedding, route, latency_ms):  # pragma: no cover
        import uuid

        self._logs.put_item(Item={
            "id": uuid.uuid4().hex, "prompt": prompt, "route": route,
            "latency_ms": str(latency_ms), "created_at": str(time.time()),
        })

    def recent_logs(self, limit=1000):  # pragma: no cover
        return self._logs.scan(Limit=limit).get("Items", [])

    def route_counts(self):  # pragma: no cover
        counts: dict[str, int] = {}
        for i in self._logs.scan().get("Items", []):
            counts[i["route"]] = counts.get(i["route"], 0) + 1
        return counts

    def route_latency(self):  # pragma: no cover
        agg: dict[str, list[float]] = {}
        for i in self._logs.scan().get("Items", []):
            agg.setdefault(i["route"], []).append(float(i["latency_ms"]))
        return {k: round(sum(v) / len(v), 2) for k, v in agg.items()}


_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        _store = DynamoStore() if settings.store_backend == "dynamodb" else SqliteStore(settings.sqlite_path)
    return _store
