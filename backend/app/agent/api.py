"""Agent API (BUILD.md §11.5)."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .. import blobstore
from ..auth import Identity, current_user
from ..config import settings
from . import datasets, integrations, runner

router = APIRouter(prefix="/api", tags=["agent"])


class RunIn(BaseModel):
    pattern_id: str
    instructions: str = ""
    dataset_ids: list[str] = Field(default_factory=list)
    integration_ids: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    max_iterations: int | None = None
    max_cost_usd: float | None = None
    start: bool = False


class TrainSourceIn(BaseModel):
    code: str = Field(min_length=1, max_length=200_000)
    dataset_ids: list[str] = Field(default_factory=list)       # retrain with more data (on top of the last run's)
    integration_ids: list[str] = Field(default_factory=list)   # attach an approved integration (feature source)


class RespondIn(BaseModel):
    action: str
    feedback: str = ""
    dataset_ids: list[str] = Field(default_factory=list)
    integration_ids: list[str] = Field(default_factory=list)
    instructions: str = ""


class BudgetIn(BaseModel):
    max_cost_usd: float | None = None
    max_iterations: int | None = None


class UploadIn(BaseModel):
    filename: str


class DatasetIn(BaseModel):
    filename: str
    key: str
    columns_map: dict[str, str]
    use: str = "both"
    pii_action: str = "redact"


class IntegrationIn(BaseModel):
    name: str
    type: str
    target: str
    role: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    module_key: str | None = None


def _own_run(run_id: str, ident: Identity) -> None:
    """Only the user who started a run (or an admin) can start, steer, accept or re-budget it."""
    try:
        run = runner.load(run_id)
    except runner.RunError as exc:
        raise _err(exc) from exc
    if run["user_id"] != ident.user_id and not ident.is_admin:
        raise HTTPException(403, "this run belongs to another user")


def _own_datasets(dataset_ids: list[str], ident: Identity) -> None:
    for ds_id in dataset_ids:
        try:
            owner = datasets.get(ds_id)["owner"]
        except datasets.DatasetError as exc:
            raise HTTPException(404, str(exc)) from exc
        if owner != ident.user_id and not ident.is_admin:
            raise HTTPException(403, f"dataset {ds_id} belongs to another user")


def _err(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", 422)
    return HTTPException(status, str(exc))


@router.get("/patterns/{pattern_id}/intake")
def intake(pattern_id: str) -> dict[str, Any]:
    try:
        return runner.intake_summary(pattern_id)
    except runner.RunError as exc:
        raise _err(exc) from exc


@router.post("/agent/runs")
def create_run(body: RunIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_datasets(body.dataset_ids, ident)
    try:
        run = runner.create_run(body.pattern_id, ident.user_id, body.model_dump(exclude={"pattern_id", "start"}))
        if body.start or settings.agent_autostart:
            run = runner.start_run(run["id"])
        return _public(run)
    except (runner.RunError, integrations.IntegrationError) as exc:
        raise _err(exc) from exc


@router.post("/patterns/{pattern_id}/train-source")
def train_source(pattern_id: str, body: TrainSourceIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    """The workspace editor's Train (and the control centre's Retrain): run the ml / dl code as a sandboxed training
    job, through an agent run with a fixed plan (dataset -> train -> evaluate -> report), so it gets the same gate
    and ledger. Trains on the last run's data plus any `dataset_ids`; `integration_ids` attach approved
    integrations (feature sources)."""
    _own_datasets(body.dataset_ids, ident)
    from sqlalchemy import select

    from .. import db
    from ..models import AgentRunRow
    from ..store import store

    p = store().get_pattern(pattern_id)
    if p is None:
        raise HTTPException(404, "pattern not found")
    if p.kind is None or p.kind.value not in ("ml", "dl"):
        raise HTTPException(409, "edited training code is for ml or dl artifacts; edit a rule's code directly")
    with db.session() as s:                      # the data the current version was trained on
        if s.scalars(select(AgentRunRow.id).where(AgentRunRow.pattern_id == pattern_id,
                                                  AgentRunRow.status.in_(("agent_running", "verifying")))).first():
            raise HTTPException(409, "a run on this pattern is still training or verifying; wait for it to finish")
        last = s.scalars(select(AgentRunRow.id).where(AgentRunRow.pattern_id == pattern_id)
                         .order_by(AgentRunRow.created_at.desc()).limit(1)).first()
    prev = runner.load(last)["intake"] if last else {}
    dataset_ids = []
    for d in prev.get("dataset_ids", []):
        try:
            if ident.is_admin or datasets.get(d)["owner"] == ident.user_id:
                dataset_ids.append(d)
        except datasets.DatasetError:
            continue                              # deleted since: train on the rest
    try:
        run = runner.create_run(pattern_id, ident.user_id, {
            "instructions": "Retrain with additional data / integrations." if body.dataset_ids or body.integration_ids
            else "Train from the edited code in the workspace editor.",
            "dataset_ids": list(dict.fromkeys([*dataset_ids, *body.dataset_ids])),
            "integration_ids": list(dict.fromkeys([*prev.get("integration_ids", []), *body.integration_ids])),
            "source_code": body.code, "source_kind": p.kind.value})
        return _public(runner.start_run(run["id"]))
    except (runner.RunError, integrations.IntegrationError) as exc:
        raise _err(exc) from exc


@router.post("/agent/runs/{run_id}/start")
def start(run_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_run(run_id, ident)
    try:
        return _public(runner.start_run(run_id))
    except runner.RunError as exc:
        raise _err(exc) from exc


@router.get("/agent/runs/{run_id}")
def get_run(run_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_run(run_id, ident)
    try:
        return _public(runner.load(run_id))
    except runner.RunError as exc:
        raise _err(exc) from exc


@router.get("/agent/runs")
def list_runs(pattern_id: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from .. import db
    from ..models import AgentRunRow

    with db.session() as s:
        q = select(AgentRunRow.id).order_by(AgentRunRow.created_at.desc()).limit(50)
        if pattern_id:
            q = q.where(AgentRunRow.pattern_id == pattern_id)
        ids = list(s.scalars(q))
    return [_public(runner.load(i), brief=True) for i in ids]


@router.post("/agent/runs/{run_id}/respond")
def respond(run_id: str, body: RespondIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_run(run_id, ident)
    _own_datasets(body.dataset_ids, ident)
    try:
        return _public(runner.respond(run_id, body.action, body.feedback, ident.user_id,
                                      intake={"dataset_ids": body.dataset_ids, "integration_ids": body.integration_ids,
                                              "instructions": body.instructions}))
    except (runner.RunError, integrations.IntegrationError) as exc:
        raise _err(exc) from exc


@router.post("/agent/runs/{run_id}/budget")
def budget(run_id: str, body: BudgetIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_run(run_id, ident)
    try:
        return _public(runner.raise_budget(run_id, body.max_cost_usd, body.max_iterations))
    except runner.RunError as exc:
        raise _err(exc) from exc


@router.get("/agent/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0,
                 ident: Identity = Depends(current_user)) -> EventSourceResponse:
    """Server-Sent Events: the run's timeline as it happens (ends when the run finishes or the client leaves)."""
    _own_run(run_id, ident)
    async def gen() -> AsyncIterator[dict[str, str]]:
        sent = after
        idle = 0
        while idle < 1200 and not await request.is_disconnected():
            run = await asyncio.to_thread(runner.load, run_id)
            new = [e for e in run.get("events", []) if e["n"] > sent]
            for e in new:
                sent = e["n"]
                yield {"event": "step", "id": str(e["n"]), "data": json.dumps(e, default=str)}
            yield {"event": "status", "data": json.dumps({"status": run["status"],
                                                          "cost_so_far_usd": run["cost_so_far_usd"]})}
            if run["status"] in ("stopped", "accepted", "not_compilable"):
                return
            idle = 0 if new else idle + 1
            await asyncio.sleep(0.5)
    return EventSourceResponse(gen())


def _public(run: dict[str, Any], brief: bool = False) -> dict[str, Any]:
    keep = ("id", "pattern_id", "user_id", "status", "cost_so_far_usd", "created_at", "updated_at", "intake", "budget",
            "iteration", "iterations", "events", "pending_question", "cap_reached", "last_candidate")
    out = {k: run.get(k) for k in keep}
    out["candidates"] = {k: {kk: v.get(kk) for kk in ("id", "kind", "params", "metrics", "confusion", "leak_warnings")}
                         for k, v in (run.get("candidates") or {}).items()}
    out["not_fully_deterministic"] = bool(run.get("integration_spec"))
    if brief:
        out.pop("events")
        out.pop("iterations")
    return out


# ----------------------------------------------------------------------------- datasets
@router.post("/datasets/upload-url")
def dataset_upload_url(body: UploadIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    safe = "".join(ch for ch in body.filename if ch.isalnum() or ch in "._-")[:120] or "data.csv"
    return blobstore.upload_url(f"uploads/datasets/{ident.user_id}/{uuid.uuid4().hex}/{safe}")


@router.post("/datasets")
def register_dataset(body: DatasetIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    if not body.key.startswith(f"uploads/datasets/{ident.user_id}/"):
        raise HTTPException(403, "that upload belongs to another user")
    try:
        return datasets.register(ident.user_id, body.filename, body.columns_map, body.use, key=body.key,
                                 pii_action=body.pii_action)
    except datasets.DatasetError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _own_datasets([dataset_id], ident)
    return datasets.get(dataset_id)


# ----------------------------------------------------------------------------- integrations
@router.post("/integrations")
def register_integration(body: IntegrationIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    try:
        module = blobstore.get(body.module_key) if body.module_key else None
        return integrations.register(ident.user_id, body.name, body.type, body.target, body.role, body.config,
                                     body.secret, module)
    except integrations.IntegrationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/integrations")
def list_integrations(status: str | None = None) -> list[dict[str, Any]]:
    return integrations.list_all(status)


@router.post("/integrations/{integration_id}/approve")
def approve_integration(integration_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    if not ident.is_admin:
        raise HTTPException(403, "admin role required")
    return integrations.decide(integration_id, ident.user_id, True)


@router.post("/integrations/{integration_id}/reject")
def reject_integration(integration_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    if not ident.is_admin:
        raise HTTPException(403, "admin role required")
    return integrations.decide(integration_id, ident.user_id, False)
