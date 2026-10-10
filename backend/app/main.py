"""FastAPI application: chat routing, light layer, patterns, token ledger, configuration."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, engine, reverify  # noqa: F401 - reverify registers the kb_event hook
from . import patterns as pat
from .auth import Identity, current_user, require_admin
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
    ToolConfig,
    VerifyRequest,
    VerifyResult,
)
from .store import store


def apply_config_overrides() -> None:
    for k, v in store().config_overrides().items():
        if k in EDITABLE:
            setattr(settings, k, v)


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
    yield
    from .runtime import _reset

    _reset()


app = FastAPI(title="Cascade", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])


from .rag.api import router as knowledge_router  # noqa: E402

app.include_router(knowledge_router)

if settings.demo:
    from .demo_api import router as demo_router

    app.include_router(demo_router)


@app.exception_handler(pat.PatternError)
async def _pattern_error(_req: Request, exc: pat.PatternError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


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
    return light_metrics().snapshot()


# ------------------------------------------------------------------ patterns
@app.get("/api/patterns", response_model=list[Pattern])
def list_patterns(status: str | None = None) -> list[Pattern]:
    return store().list_patterns(status)


@app.get("/api/patterns/{pattern_id}", response_model=Pattern)
def get_pattern(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise HTTPException(404, "pattern not found")
    return p


@app.post("/api/patterns/compile", response_model=Pattern)
def compile_pattern(req: CompileRequest, ident: Identity = Depends(current_user)) -> Pattern:
    return pat.compile_pattern(req, ident.user_id)


@app.put("/api/patterns/{pattern_id}", response_model=Pattern)
def edit_pattern(pattern_id: str, change: PatternEdit, _ident: Identity = Depends(require_admin)) -> Pattern:
    return pat.edit(pattern_id, change)


@app.post("/api/patterns/{pattern_id}/verify", response_model=VerifyResult)
def verify_pattern(pattern_id: str, req: VerifyRequest) -> VerifyResult:
    return pat.verify_pattern(pattern_id, req.samples or None)


@app.post("/api/patterns/{pattern_id}/approve", response_model=Pattern)
def approve_pattern(pattern_id: str, ident: Identity = Depends(require_admin)) -> Pattern:
    return pat.approve(pattern_id, ident.user_id)


@app.post("/api/patterns/{pattern_id}/reject", response_model=Pattern)
def reject_pattern(pattern_id: str, _ident: Identity = Depends(require_admin)) -> Pattern:
    return pat.reject(pattern_id)


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
    if "llm" in update.values:
        from .heavy import set_heavy_model

        set_heavy_model(None)
    return _config()
