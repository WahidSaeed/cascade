"""Phase 7 (BUILD.md §13): the hot exact cache in ElastiCache (Redis / Valkey protocol), in front of the database.

The database stays the source of truth. A hit in the database is copied here with a short TTL (never past the
entry's own expiry); invalidations (artifact edit / reject / demote, demo reset, retention) delete the copies.
Every operation fails open: if the cache is unreachable, lookups go to the database as before.
ElastiCache Serverless runs in cluster mode, where a command naming several keys in different hash slots fails
(CROSSSLOT), so every command names one key; batches go through a non-transactional pipeline (one round trip).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from ..config import settings

log = logging.getLogger("cascade.hotcache")
PREFIX = "cascade:exact:"


class HotExactCache:
    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _key(exact_key: str) -> str:
        return PREFIX + exact_key

    def get_many(self, exact_keys: list[str]) -> list[dict[str, Any] | None]:
        try:
            pipe = self._client.pipeline(transaction=False)
            for k in exact_keys:
                pipe.get(self._key(k))
            raw = pipe.execute()
        except Exception as exc:  # noqa: BLE001 - fail open to the database
            log.warning("hot cache get failed: %s", exc)
            return [None] * len(exact_keys)
        out: list[dict[str, Any] | None] = []
        for r in raw:
            try:
                out.append(json.loads(r) if r else None)
            except ValueError:
                out.append(None)
        return out

    def put(self, exact_key: str, entry: dict[str, Any], expires_at: float) -> None:
        ttl = int(min(settings.exact_cache_ttl_s, expires_at - time.time()))
        if ttl <= 0:
            return
        try:
            self._client.set(self._key(exact_key), json.dumps(entry), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning("hot cache put failed: %s", exc)

    def _delete_keys(self, keys: list[str]) -> None:
        for i in range(0, len(keys), 500):
            pipe = self._client.pipeline(transaction=False)
            for k in keys[i:i + 500]:
                pipe.delete(k)
            pipe.execute()

    def delete(self, exact_keys: list[str]) -> None:
        if not exact_keys:
            return
        try:
            self._delete_keys([self._key(k) for k in exact_keys])
        except Exception as exc:  # noqa: BLE001
            log.warning("hot cache delete failed: %s", exc)

    def flush(self) -> None:
        """Drop every Cascade key (demo reset, retention): the database is the truth again."""
        try:
            self._delete_keys(list(self._client.scan_iter(match=PREFIX + "*", count=500)))
        except Exception as exc:  # noqa: BLE001
            log.warning("hot cache flush failed: %s", exc)


_lock = threading.Lock()
_hot: dict[str, Any] = {"url": None, "cache": None}


def hot_cache() -> HotExactCache | None:
    """The configured hot cache (CASCADE_EXACT_CACHE_URL, e.g. rediss://…:6379), or None."""
    url = settings.exact_cache_url
    if not url:
        return None
    with _lock:
        if _hot["url"] != url:
            import redis

            client = redis.Redis.from_url(url, socket_timeout=0.05, socket_connect_timeout=0.2,
                                          health_check_interval=30)
            _hot.update(url=url, cache=HotExactCache(client))
        return _hot["cache"]  # type: ignore[no-any-return]


def set_hot_cache(cache: HotExactCache | None, url: str = "test://") -> None:
    """Tests: install a cache backed by a fake client."""
    with _lock:
        _hot.update(url=url if cache else None, cache=cache)
