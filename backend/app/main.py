"""FastAPI application."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .store import store


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.migrate()
    yield


app = FastAPI(title="Cascade", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"],
                   allow_headers=["*"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "db": db.dialect_name(), "llm": settings.llm}


@app.get("/api/users")
def users() -> list[dict[str, object]]:
    return store().list_users(personas_only=True)
