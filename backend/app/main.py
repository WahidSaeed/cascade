"""FastAPI application: chat routing + the pattern review workflow + config + metrics."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import engine, patterns as patterns_mod, verifier
from .config import settings
from .schemas import (ChatRequest, ChatResponse, Metrics, Pattern, PatternEdit,
                      PatternStatus, ToolConfig, VerifyResult)
from .store import store

app = FastAPI(title="Cascade", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


# ------------------------------------------------------------------ chat
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.prompt.strip():
        raise HTTPException(400, "empty prompt")
    return engine.route(req.prompt)


# ------------------------------------------------------------------ patterns
class CompileRequest(BaseModel):
    name: str
    description: str
    examples: list[str] = []


class VerifyRequest(BaseModel):
    samples: list[str] = []


@app.get("/api/patterns", response_model=list[Pattern])
def list_patterns() -> list[Pattern]:
    return store().list_patterns()


@app.get("/api/patterns/{pattern_id}", response_model=Pattern)
def get_pattern(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if not p:
        raise HTTPException(404, "pattern not found")
    return p


@app.post("/api/patterns/compile", response_model=Pattern)
def compile_pattern(req: CompileRequest) -> Pattern:
    p = patterns_mod.compile_handler(req.name, req.description, req.examples)
    return store().put_pattern(p)


@app.put("/api/patterns/{pattern_id}", response_model=Pattern)
def edit_pattern(pattern_id: str, edit: PatternEdit) -> Pattern:
    p = store().get_pattern(pattern_id)
    if not p:
        raise HTTPException(404, "pattern not found")
    changed = edit.model_dump(exclude_none=True)
    for k, v in changed.items():
        setattr(p, k, v)
    if "handler_code" in changed:
        # editing the code invalidates prior verification and pulls it back to draft
        p.version += 1
        p.verification_score = None
        p.status = PatternStatus.draft
    return store().put_pattern(p)


@app.post("/api/patterns/{pattern_id}/verify", response_model=VerifyResult)
def verify_pattern(pattern_id: str, req: VerifyRequest) -> VerifyResult:
    p = store().get_pattern(pattern_id)
    if not p:
        raise HTTPException(404, "pattern not found")
    result = verifier.verify(p, req.samples)
    p.verification_score = result.score
    if result.passed and p.status == PatternStatus.draft:
        p.status = PatternStatus.shadow  # verified; eligible for approval
    store().put_pattern(p)
    return result


@app.post("/api/patterns/{pattern_id}/approve", response_model=Pattern)
def approve_pattern(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if not p:
        raise HTTPException(404, "pattern not found")
    if p.verification_score is None or p.verification_score < settings.verify_min_score:
        raise HTTPException(
            409,
            f"cannot approve: verification score {p.verification_score} "
            f"below required {settings.verify_min_score}. Verify first.",
        )
    # In production (handler_runtime=lambda) this is where the handler is deployed as a
    # Lambda function (see infra/ and deploy/deploy_handler.py). TODO: wire deploy call.
    p.status = PatternStatus.approved
    return store().put_pattern(p)


@app.post("/api/patterns/{pattern_id}/reject", response_model=Pattern)
def reject_pattern(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if not p:
        raise HTTPException(404, "pattern not found")
    p.status = PatternStatus.rejected
    return store().put_pattern(p)


# ------------------------------------------------------------------ config
@app.get("/api/config", response_model=ToolConfig)
def get_config() -> ToolConfig:
    return ToolConfig(
        heavy_backend=settings.heavy_backend,
        bedrock_model_id=settings.bedrock_model_id,
        embed_backend=settings.embed_backend,
        cache_threshold=settings.cache_threshold,
        pattern_threshold=settings.pattern_threshold,
        handler_runtime=settings.handler_runtime,
        verify_min_score=settings.verify_min_score,
    )


@app.put("/api/config", response_model=ToolConfig)
def update_config(cfg: ToolConfig) -> ToolConfig:
    # in-memory for v0; persist to SSM Parameter Store in production
    for field, value in cfg.model_dump().items():
        setattr(settings, field, value)
    return get_config()


# ------------------------------------------------------------------ metrics
@app.get("/api/metrics", response_model=Metrics)
def metrics() -> Metrics:
    st = store()
    counts = st.route_counts()
    total = sum(counts.values())
    cheap = counts.get("cache", 0) + counts.get("fast_path", 0)
    pats = st.list_patterns()
    return Metrics(
        total_requests=total,
        by_route=counts,
        cheap_share=round(cheap / total, 4) if total else 0.0,
        avg_latency_ms=st.route_latency(),
        approved_patterns=sum(p.status == PatternStatus.approved for p in pats),
        draft_patterns=sum(p.status == PatternStatus.draft for p in pats),
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "store": settings.store_backend, "heavy": settings.heavy_backend}


# ------------------------------------------------------------------ frontend
if FRONTEND.exists():
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    app.mount("/", StaticFiles(directory=str(FRONTEND)), name="frontend")
