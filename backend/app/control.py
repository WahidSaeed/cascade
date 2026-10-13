"""Admin control centre for one artifact: version history (with each version's code for diffs), rollback, and
usage across users. Retraining with more data / an integration goes through POST /api/patterns/{id}/train-source
(ml / dl) or an agent run (rules)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from . import db
from . import patterns as pat
from .auth import Identity, current_user, require_admin
from .models import LedgerRollup
from .store import store

router = APIRouter(prefix="/api", tags=["control"])


def _pattern(pattern_id: str) -> Any:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise HTTPException(404, "pattern not found")
    return p


@router.get("/patterns/{pattern_id}/versions")
def versions(pattern_id: str) -> list[dict[str, Any]]:
    """Every artifact version, newest first: what produced it, its verification and whether it was approved."""
    p = _pattern(pattern_id)
    out = []
    for v in store().pattern_versions(pattern_id):
        d = v.data
        out.append({"version": v.version, "kind": v.kind, "status": v.status, "current": v.version == p.version,
                    "change_note": d.get("change_note", ""), "approved_by": d.get("approved_by"),
                    "verification_score": d.get("verification_score"),
                    "verification_passed": bool((d.get("verification") or {}).get("passed")),
                    "created_at": v.created_at, "updated_at": v.updated_at})
    return out


@router.get("/patterns/{pattern_id}/versions/{version}")
def version(pattern_id: str, version: int) -> dict[str, Any]:
    _pattern(pattern_id)
    v = store().pattern_version(pattern_id, version)
    if v is None:
        raise HTTPException(404, f"v{version} not found")
    d = v.data
    return {"version": v.version, "kind": v.kind, "status": v.status, "change_note": d.get("change_note", ""),
            "handler_code": d.get("handler_code", ""), "source_code": d.get("source_code", ""),
            "model_card": d.get("model_card", {}), "verification": d.get("verification"),
            "created_at": v.created_at}


class RollbackIn(BaseModel):
    version: int


@router.post("/patterns/{pattern_id}/rollback")
def rollback(pattern_id: str, body: RollbackIn, ident: Identity = Depends(require_admin)) -> dict[str, Any]:
    """Restore an earlier version as a new version and redeploy it (admin only)."""
    try:
        p, pending = pat.rollback(pattern_id, body.version, ident.user_id)
    except pat.PatternError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"version": p.version, "status": p.status.value, "change_note": p.change_note, "pending": pending}


@router.get("/patterns/{pattern_id}/usage")
def usage(pattern_id: str, _ident: Identity = Depends(current_user)) -> dict[str, Any]:
    """Who this pattern serves: every user whose requests matched it, answered by the artifact or (before it
    was approved, or when it deferred) by the LLM. Read from the hourly ledger rollups."""
    _pattern(pattern_id)
    r = LedgerRollup
    with db.read_session() as s:
        rows = s.execute(select(
            r.user_id, r.route, func.sum(r.requests), func.sum(r.tokens_avoided_in + r.tokens_avoided_out),
            func.sum(r.cost_avoided_usd), func.sum(r.runtime_cost_usd), func.sum(r.cost_actual_usd), func.max(r.bucket))
            .where(r.pattern_id == pattern_id).group_by(r.user_id, r.route)).all()
    users: dict[str, dict[str, Any]] = {}
    for uid, route, n, avoided, saved, runtime, spent, last in rows:
        u = users.setdefault(uid, {"user_id": uid, "artifact_requests": 0, "llm_requests": 0, "other_requests": 0,
                                   "tokens_avoided": 0, "cost_avoided_usd": 0.0, "cost_usd": 0.0, "last_bucket": 0})
        key = {"artifact": "artifact_requests", "llm": "llm_requests"}.get(route, "other_requests")
        u[key] += int(n or 0)
        u["tokens_avoided"] += int(avoided or 0)
        u["cost_avoided_usd"] += float(saved or 0.0)
        u["cost_usd"] += float(runtime or 0.0) + float(spent or 0.0)
        u["last_bucket"] = max(u["last_bucket"], int(last or 0))
    names = {u["id"]: u.get("name") or u["id"] for u in store().list_users()}
    per_user = sorted(users.values(), key=lambda u: -(u["artifact_requests"] + u["llm_requests"]))
    for u in per_user:
        u["name"] = names.get(u["user_id"], u["user_id"])
        u["cost_avoided_usd"] = round(u["cost_avoided_usd"], 6)
        u["cost_usd"] = round(u["cost_usd"], 6)
    served = [u for u in per_user if u["artifact_requests"]]
    return {"users": len(per_user), "users_served_by_artifact": len(served),
            "artifact_requests": sum(u["artifact_requests"] for u in per_user),
            "llm_requests": sum(u["llm_requests"] for u in per_user),
            "tokens_avoided": sum(u["tokens_avoided"] for u in per_user),
            "cost_avoided_usd": round(sum(u["cost_avoided_usd"] for u in per_user), 6),
            "per_user": per_user}
