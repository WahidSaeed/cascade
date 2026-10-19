"""Discovery (BUILD.md §10): find recurring patterns in recent LLM traffic.

1. Take recent `llm`-route requests (their stored embeddings, templates and PII-redacted prompts).
2. Greedy cosine clustering (minimum size `discovery_min`).
3. Skip clusters that existing patterns already cover (centroid or template overlap).
4. The light LLM names each cluster and classifies its task type (recorded as `discovery` overhead).
5. For compilable task types only, create a `recognized` pattern ranked by projected token savings
   (cluster frequency x measured LLM tokens per request) and notify users (it opens the intake).
   Free-form questions over the same sources are left to the shared cache; a knowledge cluster that
   varies only by slot values over the same documents is proposed as a `lookup`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select

from . import db
from .config import settings
from .heavy import LLMRequest, light_llm, parse_json_reply
from .light.normalize import normalize
from .light.vec import centroid, cosine
from .models import LedgerRow, Notification, RequestLog
from .pricing import cost
from .schemas import COMPILABLE_TASK_TYPES, Pattern, PatternStatus, TaskType
from .store import store
from .verifier import record_llm_overhead

DAY = 86400


@dataclass
class Cluster:
    centroid: np.ndarray
    members: list[int] = field(default_factory=list)
    seeds: list[np.ndarray] = field(default_factory=list)     # centroids of the template groups merged in


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) or 1.0)


def _cluster(vectors: np.ndarray, template_keys: list[str], threshold: float, merge: float = 0.895) -> list[Cluster]:
    """Template-first clustering: prompts sharing a template seed a cluster; seeds whose centroids are close
    merge; the remaining prompts join the nearest centroid at >= `threshold`, or start a new cluster."""
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(template_keys):
        groups.setdefault(k, []).append(i)
    clusters: list[Cluster] = []
    singles: list[int] = []
    for idx in groups.values():
        if len(idx) >= 2:
            clusters.append(Cluster(_unit(vectors[idx].mean(axis=0)), list(idx)))
        else:
            singles += idx
    merged: list[Cluster] = []
    for c in sorted(clusters, key=lambda c: -len(c.members)):
        # complete linkage: a template group joins only if it is close to EVERY group already merged, so a
        # merged centroid can't drift towards a neighbouring task
        target = next((m for m in merged if min(float(np.dot(sd, c.centroid)) for sd in m.seeds) >= merge), None)
        if target is None:
            c.seeds = [c.centroid.copy()]
            merged.append(c)
        else:
            target.members += c.members
            target.seeds.append(c.centroid.copy())
            target.centroid = _unit(vectors[target.members].mean(axis=0))
    for i in singles:
        v = vectors[i]
        best = max(merged, key=lambda c: float(np.dot(c.centroid, v)), default=None)
        if best is not None and float(np.dot(best.centroid, v)) >= threshold:
            best.members.append(i)
            n = len(best.members)
            best.centroid = _unit(best.centroid * (n - 1) / n + v / n)
        else:
            merged.append(Cluster(v.copy(), [i]))
    return merged


def _covered(cent: list[float], template_keys: set[str], patterns: list[Pattern],
             task_type: str | None = None) -> Pattern | None:
    """An existing pattern already covers the cluster: shared templates, or (once the cluster is labelled)
    a near-identical centroid with the same task type — a routing cluster near a triage pattern is new."""
    for p in patterns:
        if p.status in (PatternStatus.rejected,):
            continue
        if template_keys & set(p.template_keys):
            return p
        if task_type is not None and p.task_type is not None and p.task_type.value == task_type \
                and p.centroid and cosine(cent, p.centroid) >= settings.pattern_threshold + 0.05:
            return p
    return None


def discover(window_days: float = 7.0, now: float | None = None, notify: bool = True) -> list[Pattern]:
    t_now = now or time.time()
    since = t_now - window_days * DAY
    with db.session() as s:
        rows = s.execute(select(RequestLog.request_id, RequestLog.redacted, RequestLog.template_key, RequestLog.embedding,
                                RequestLog.ts, RequestLog.knowledge, RequestLog.collection_ids)
                         .where(RequestLog.route == "llm", RequestLog.ts >= since, RequestLog.embedding.is_not(None))
                         .order_by(RequestLog.ts)).all()
        usage = {rid: (tin, tout) for rid, tin, tout in s.execute(
            select(LedgerRow.request_id, LedgerRow.input_tokens, LedgerRow.output_tokens)
            .where(LedgerRow.route == "llm", LedgerRow.ts >= since))}
    rows = [r for r in rows if not normalize(r[1]).context_dependent]
    if not rows:
        return []
    vecs = np.asarray([r[3] for r in rows], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    clusters = [c for c in _cluster(vecs, [r[2] for r in rows], settings.discovery_similarity)
                if len(c.members) >= settings.discovery_min]
    existing = store().list_patterns()
    span_days = max(1.0, (rows[-1][4] - rows[0][4]) / DAY) if len(rows) > 1 else 1.0
    created: list[Pattern] = []
    for c in sorted(clusters, key=lambda c: -len(c.members)):
        members = [rows[i] for i in c.members]
        cent = [float(x) for x in c.centroid]
        tkeys = {m[2] for m in members}
        if _covered(cent, tkeys, existing + created):
            continue
        examples = [m[1] for m in members]
        sample = examples[:: max(1, len(examples) // 12)][:12]
        res = light_llm().complete(LLMRequest(
            question="Name this cluster of requests and classify its task type (classification, routing, extraction, "
                     "transform, scoring, lookup, generation or qa). Reply as JSON {name, task_type, coherent}.\n"
                     + "\n".join(f"- {e}" for e in sample),
            purpose="name_cluster", meta={"examples": sample}, model_id=settings.light_llm_model_id
            if settings.llm == "bedrock" else None))
        record_llm_overhead(res, "discovery", None, note=f"cluster of {len(members)}")
        try:
            label = parse_json_reply(res.text)
        except ValueError:
            continue
        ttype = str(label.get("task_type", ""))
        if ttype not in COMPILABLE_TASK_TYPES or not label.get("coherent", True):
            continue                                   # generation / free-form Q&A stay on the LLM and cache
        if _covered(cent, tkeys, existing + created, ttype):
            continue                                   # generation / free-form Q&A stay on the LLM and cache
        measured = [usage[m[0]] for m in members if m[0] in usage]
        avg_in = float(np.mean([u[0] for u in measured])) if measured else 0.0
        avg_out = float(np.mean([u[1] for u in measured])) if measured else 0.0
        per_day = len(members) / span_days
        tokens_per_day = per_day * (avg_in + avg_out)
        monthly_usd = 30 * per_day * cost(settings.heavy_model_id, int(avg_in), int(avg_out))
        p = Pattern(name=str(label.get("name", "Recognized pattern")), description=f"Recognized from {len(members)} "
                    f"LLM requests", task_type=TaskType(ttype), status=PatternStatus.recognized, centroid=cent,
                    exemplars=[[float(x) for x in vecs[i]] for i in c.members[:24]],
                    template_keys=sorted(tkeys)[:50], templates=sorted({normalize(e).template for e in examples})[:20],
                    examples=examples[:50], avg_input_tokens=round(avg_in, 2), avg_output_tokens=round(avg_out, 2),
                    samples_measured=len(measured), frequency_per_day=round(per_day, 2),
                    projected_tokens_per_day=round(tokens_per_day, 1),
                    projected_monthly_savings_usd=round(monthly_usd, 4))
        p.model_card = {"discovery": {"cluster_size": len(members), "knowledge": sum(bool(m[5]) for m in members),
                                      "collections": sorted({cid for m in members for cid in (m[6] or [])})}}
        store().put_pattern(p)
        created.append(p)
        if notify:
            _notify(p)
    return sorted(created, key=lambda p: -p.projected_tokens_per_day)


def _notify(p: Pattern) -> None:
    with db.session() as s:
        s.add(Notification(user_id=None, kind="pattern_recognized",
                           title=f"New pattern recognized: {p.name} — projected savings ~{int(p.projected_tokens_per_day):,} "
                                 f"tokens/day",
                           body=f"{p.frequency_per_day:.0f} requests/day · {p.task_type.value if p.task_type else ''}",
                           link=f"/patterns/intake/?id={p.id}", ref_id=p.id, created_at=time.time()))


def notifications(user_id: str | None = None, unread_only: bool = False) -> list[dict[str, Any]]:
    with db.session() as s:
        q = select(Notification).order_by(Notification.created_at.desc()).limit(50)
        rows = [n for n in s.scalars(q) if n.user_id in (None, user_id) and (not unread_only or not n.read)]
        return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "ref_id": n.ref_id,
                 "created_at": n.created_at, "read": n.read} for n in rows]


def mark_read(notification_id: str, user_id: str) -> bool:
    """Only the addressee (or anyone, for broadcast notifications) can mark a notification read."""
    with db.session() as s:
        n = s.get(Notification, notification_id)
        if n is None or n.user_id not in (None, user_id):
            return False
        n.read = True
        return True


__all__ = ["discover", "notifications", "mark_read", "centroid"]
