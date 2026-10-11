"""FastAPI application: chat routing, light layer, patterns, token ledger, configuration."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError

from . import db, engine, reverify  # noqa: F401 - reverify registers the kb_event hook
from . import patterns as pat
from .auth import Identity, current_user, require_admin
from .cognito import CognitoMiddleware
from .config import EDITABLE, settings
from .ledger import ledger
from .light.metrics import light_metrics
from .schemas import (
    ChatRequest,
    ChatResponse,
    CompileRequest,
    ConfigUpdate,
    Pattern,
    PatternEdit,
    PatternStatus,
    ToolConfig,
    VerifyRequest,
    VerifyResult,
)
from .store import store


def apply_config_overrides() -> None:
    from . import tasksync

    overrides = store().config_overrides()
    for k, v in overrides.items():
        if k in EDITABLE:
            setattr(settings, k, v)
    tasksync.mark_applied(overrides)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.migrate()
    apply_config_overrides()
    from .light.embedder import get_embedder

    get_embedder()            # load the light-layer model once, at startup
    import threading

    from .light.cache import light_cache
    from .runtime import warm

    light_cache().ensure_loaded()
    threading.Thread(target=warm, args=(store().approved_patterns(),), daemon=True).start()
    from . import scheduler

    scheduler.start()
    yield
    from .runtime import _reset

    _reset()


app = FastAPI(title="Cascade", version="0.4.0", lifespan=lifespan)
# Production: CORS only from the CloudFront origin of the static web export (CASCADE_CORS_ORIGINS).
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
@app.middleware("http")
async def _config_sync(request: Request, call_next: Any) -> Any:
    """Pick up configuration edits made through another API task (Phase 7, app/tasksync.py)."""
    from . import tasksync

    if tasksync.due():
        await run_in_threadpool(tasksync.maybe_sync)
    return await call_next(request)


app.add_middleware(CognitoMiddleware)            # CASCADE_AUTH=cognito: 401 without a valid JWT (not /api/health)
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"],
                   allow_headers=["Authorization", "Content-Type", "X-User-Id"], allow_credentials="*" not in _origins)


from .agent.api import router as agent_router  # noqa: E402
from .rag.api import router as knowledge_router  # noqa: E402

app.include_router(knowledge_router)
app.include_router(agent_router)

if settings.demo:
    from .demo_api import router as demo_router

    app.include_router(demo_router)


@app.exception_handler(pat.PatternError)
async def _pattern_error(_req: Request, exc: pat.PatternError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(DBAPIError)
async def _db_error(_req: Request, exc: DBAPIError) -> JSONResponse:
    """A connection broken by a database failover: SQLAlchemy has already invalidated the pool and nothing
    was committed, so the client may safely retry (the web client does, once)."""
    if exc.connection_invalidated:
        return JSONResponse(status_code=503, content={"detail": "database connection was reset; retry"},
                            headers={"Retry-After": "1"})
    raise exc


# ------------------------------------------------------------------ health / users
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "db": db.dialect_name(), "llm": settings.llm, "auth": settings.auth,
            "demo": settings.demo, "artifact_runtime": settings.artifact_runtime}


@app.get("/api/users")
def users() -> list[dict[str, Any]]:
    return store().list_users(personas_only=True)


@app.get("/api/me")
def me(ident: Identity = Depends(current_user)) -> dict[str, Any]:
    return {"user_id": ident.user_id, "groups": ident.groups, "is_admin": ident.is_admin,
            "principals": ident.principals}


# ------------------------------------------------------------------ chat
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, ident: Identity = Depends(current_user)) -> ChatResponse:
    if not req.prompt.strip():
        raise HTTPException(400, "empty prompt")
    if len(req.prompt) > 20_000:
        raise HTTPException(413, "prompt too long")
    return engine.route(req.prompt, ident.user_id, req.conversation_id,
                        req.collection_ids if req.collection_ids else None)


@app.get("/api/light/metrics")
def light_metrics_endpoint() -> dict[str, Any]:
    from .light.recognizer import ClassifierRecognizer, recognizer
    from .monitor import false_hit_rate

    snap = light_metrics().snapshot(false_hit_rate())
    r = recognizer()
    snap["recognizer"] = {"type": type(r).__name__,
                          **(r.meta if isinstance(r, ClassifierRecognizer) else {})}
    return snap


# ------------------------------------------------------------------ patterns
def _with_hits(patterns: list[Pattern]) -> list[Pattern]:
    """`hits` = artifact invocations, counted from the ledger rollups (never a per-request write to the
    pattern row, which every API task would contend on)."""
    counts = ledger().artifact_hits()
    for p in patterns:
        p.hits = counts.get(p.id, 0)
        if not p.source_code and p.kind is not None and p.kind.value in ("ml", "dl"):
            p.source_code = _generated_source(p)
    return patterns


_sources: dict[tuple[str, int], str] = {}


def _generated_source(p: Pattern) -> str:
    """ml / dl artifacts installed before their Python was recorded: render it from the model's meta.json."""
    from .compilers.source import artifact_source

    key = (p.id, p.version)
    if key not in _sources:
        meta_file = Path(p.model_uri) / "meta.json"
        if p.model_uri.startswith("s3://") or not meta_file.exists():
            return ""
        meta = json.loads(meta_file.read_text())
        card = p.model_card or {}
        _sources[key] = artifact_source(p.kind.value if p.kind else "", meta, card.get("params") or {}, p.name,
                                        p.version, int(card.get("training_size") or 0))
    return _sources[key]


@app.get("/api/patterns", response_model=list[Pattern])
def list_patterns(status: str | None = None) -> list[Pattern]:
    return _with_hits(store().list_patterns(status))


@app.get("/api/patterns/{pattern_id}", response_model=Pattern)
def get_pattern(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise HTTPException(404, "pattern not found")
    return _with_hits([p])[0]


@app.post("/api/patterns/compile", response_model=Pattern)
def compile_pattern(req: CompileRequest, ident: Identity = Depends(current_user)) -> Pattern:
    return _with_hits([pat.compile_pattern(req, ident.user_id)])[0]


@app.put("/api/patterns/{pattern_id}", response_model=Pattern)
def edit_pattern(pattern_id: str, change: PatternEdit, _ident: Identity = Depends(require_admin)) -> Pattern:
    return _with_hits([pat.edit(pattern_id, change)])[0]


@app.post("/api/patterns/{pattern_id}/verify", response_model=VerifyResult)
def verify_pattern(pattern_id: str, req: VerifyRequest) -> VerifyResult:
    return pat.verify_pattern(pattern_id, req.samples or None)


class TryRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)


@app.post("/api/patterns/{pattern_id}/try")
def try_pattern(pattern_id: str, req: TryRequest, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    """The workspace editor's Test panel: run the saved artifact on one prompt, out of process (never here).
    A version no admin has approved yet runs only for admins (it may be someone's unreviewed code)."""
    from .runtime import runtime

    p = get_pattern(pattern_id)
    if p.status != PatternStatus.approved and not ident.is_admin:
        raise HTTPException(403, "only admins can run an artifact that is not approved yet")
    if p.kind is None or (p.kind.value == "rule" and not p.handler_code) or (p.kind.value != "rule" and not p.model_uri):
        raise HTTPException(409, "this pattern has no compiled artifact yet")
    r = runtime().invoke(p, req.prompt)
    return {"result": r.result, "confidence": r.confidence, "latency_ms": round(r.latency_ms, 2), "error": r.error,
            "fallback": not r.usable, "min_confidence": settings.artifact_min_confidence}


@app.post("/api/patterns/{pattern_id}/approve", response_model=Pattern)
def approve_pattern(pattern_id: str, ident: Identity = Depends(require_admin)) -> Pattern:
    return _with_hits([pat.approve(pattern_id, ident.user_id)])[0]


@app.post("/api/patterns/{pattern_id}/reject", response_model=Pattern)
def reject_pattern(pattern_id: str, _ident: Identity = Depends(require_admin)) -> Pattern:
    return _with_hits([pat.reject(pattern_id)])[0]


@app.post("/api/patterns/discover", response_model=list[Pattern])
def discover_patterns(window_days: float = 7.0, _ident: Identity = Depends(require_admin)) -> list[Pattern]:
    from .discovery import discover

    return discover(window_days)


@app.get("/api/notifications")
def notifications(ident: Identity = Depends(current_user), unread: bool = False) -> list[dict[str, Any]]:
    from .discovery import notifications as list_notifications

    return list_notifications(ident.user_id, unread)


@app.post("/api/notifications/{notification_id}/read")
def notification_read(notification_id: str, ident: Identity = Depends(current_user)) -> dict[str, str]:
    from .discovery import mark_read

    if not mark_read(notification_id, ident.user_id):
        raise HTTPException(404, "notification not found")
    return {"status": "read"}


@app.post("/api/drift/run")
def drift_run(pattern_id: str | None = None, n: int = 20,
              _ident: Identity = Depends(require_admin)) -> list[dict[str, Any]]:
    from .monitor import run_check

    return run_check(pattern_id, n)


@app.get("/api/patterns/{pattern_id}/drift")
def pattern_drift(pattern_id: str) -> dict[str, Any]:
    from .monitor import agreement, recent_samples

    p = get_pattern(pattern_id)
    score, n = agreement(p.id, p.version)
    return {"agreement": score, "samples": n, "min_agreement": settings.drift_min_agreement,
            "recent": recent_samples(p.id)}


# ------------------------------------------------------------------ ledger
@app.get("/api/ledger/summary")
def ledger_summary(frm: float | None = None, to: float | None = None, user_id: str | None = None,
                   pattern_id: str | None = None, collection_id: str | None = None) -> dict[str, Any]:
    return ledger().summary(frm, to, user_id, pattern_id, collection_id)


@app.get("/api/ledger/timeseries")
def ledger_timeseries(bucket: str = "day", frm: float | None = None, to: float | None = None,
                      user_id: str | None = None, pattern_id: str | None = None) -> list[dict[str, Any]]:
    if bucket not in ("hour", "day"):
        raise HTTPException(422, "bucket must be hour or day")
    return ledger().timeseries(bucket, frm, to, user_id, pattern_id)


@app.get("/api/ledger/by-user")
def ledger_by_user(frm: float | None = None, to: float | None = None) -> list[dict[str, Any]]:
    return ledger().by_user(frm, to)


@app.get("/api/ledger/by-pattern")
def ledger_by_pattern(frm: float | None = None, to: float | None = None) -> list[dict[str, Any]]:
    names = {p.id: p.name for p in store().list_patterns()}
    rows = ledger().by_pattern(frm, to)
    for r in rows:
        r["pattern_name"] = names.get(r["pattern_id"], r["pattern_id"])
    return rows


@app.get("/api/ledger/by-collection")
def ledger_by_collection(frm: float | None = None, to: float | None = None) -> list[dict[str, Any]]:
    return ledger().by_collection(frm, to)


@app.get("/api/ledger/entries")
def ledger_entries(limit: int = 100, user_id: str | None = None) -> list[dict[str, Any]]:
    return ledger().entries(min(limit, 1000), user_id)


# ------------------------------------------------------------------ config (Tools)
def _config() -> ToolConfig:
    return ToolConfig(values={k: getattr(settings, k) for k in sorted(EDITABLE)}, editable=sorted(EDITABLE))


@app.get("/api/config", response_model=ToolConfig)
def get_config() -> ToolConfig:
    return _config()


@app.put("/api/config", response_model=ToolConfig)
def update_config(update: ConfigUpdate, _ident: Identity = Depends(require_admin)) -> ToolConfig:
    bad = sorted(set(update.values) - EDITABLE)
    if bad:
        raise HTTPException(422, f"not editable: {bad}")
    for k, v in update.values.items():
        try:
            setattr(settings, k, v)
        except Exception as exc:  # pydantic validation
            raise HTTPException(422, f"{k}: {exc}") from exc
        store().set_config(k, getattr(settings, k))
    from . import tasksync

    tasksync.mark_applied(store().config_overrides())
    if "llm" in update.values:
        from .heavy import set_heavy_model

        set_heavy_model(None)
    return _config()
