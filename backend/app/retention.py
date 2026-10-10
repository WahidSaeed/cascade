"""Retention (BUILD.md §12, rule 6: logs carry a retention TTL).

A scheduled job deletes, past their TTL: request logs (raw prompts included), conversation turns,
conversations left without turns, drift samples (they hold raw prompts too), and expired cache entries
(plus their in-memory index entries).
It deletes nothing else — ledger rows, overhead, patterns, documents and runs stay. S3 lifecycle rules
cover datasets and old document versions (infra/terraform/site.tf).

  python -m app.retention          (EventBridge -> ECS in AWS; the local scheduler runs it daily)
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import delete, exists, select

from . import db
from .config import settings
from .models import CacheEntry, Conversation, DriftSample, RequestLog, Turn


def run(now: float | None = None) -> dict[str, Any]:
    t = now if now is not None else time.time()
    with db.session() as s:
        logs = s.execute(delete(RequestLog).where(RequestLog.expires_at.is_not(None), RequestLog.expires_at <= t))
        turns = s.execute(delete(Turn).where(Turn.expires_at.is_not(None), Turn.expires_at <= t))
        cache = s.execute(delete(CacheEntry).where(CacheEntry.expires_at <= t))
        drift = s.execute(delete(DriftSample).where(DriftSample.ts <= t - settings.log_retention_days * 86400))
        empty = s.execute(delete(Conversation).where(
            ~exists(select(Turn.id).where(Turn.conversation_id == Conversation.id)),
            Conversation.created_at <= t - 86400))
        out = {"request_logs": logs.rowcount, "turns": turns.rowcount, "cache_entries": cache.rowcount,  # type: ignore[attr-defined]
               "conversations": empty.rowcount, "drift_samples": drift.rowcount}  # type: ignore[attr-defined]
    if out["cache_entries"]:
        from .light.cache import light_cache

        light_cache().reset()          # the in-memory cache index is rebuilt from what remains
    return out


if __name__ == "__main__":  # pragma: no cover
    db.migrate()
    print(json.dumps(run()))
