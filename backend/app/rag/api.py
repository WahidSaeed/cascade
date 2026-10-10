"""Knowledge API (BUILD.md §9.6)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from .. import blobstore, db
from ..auth import Identity, current_user
from ..config import settings
from ..models import ChunkRow, CollectionRow, DocumentRow, LedgerRow
from ..store import store
from . import ingest, service
from .access import collection_acl, readable_collection_ids

router = APIRouter(prefix="/api", tags=["knowledge"])


class CollectionIn(BaseModel):
    name: str
    access: str = "org"
    group_ids: list[str] = Field(default_factory=list)


class CollectionPatch(BaseModel):
    name: str | None = None
    access: str | None = None
    group_ids: list[str] | None = None


class UploadUrlIn(BaseModel):
    collection_id: str
    filename: str


class DocumentIn(BaseModel):
    collection_id: str
    title: str
    filename: str
    key: str


class SearchIn(BaseModel):
    query: str
    collection_ids: list[str] = Field(default_factory=list)
    k: int | None = None


class GroupsIn(BaseModel):
    groups: list[str]


def _collection(collection_id: str) -> CollectionRow:
    with db.session() as s:
        c = s.get(CollectionRow, collection_id)
        if c is None:
            raise HTTPException(404, "collection not found")
        return c


def _require_read(ident: Identity, collection_id: str) -> None:
    if collection_id not in readable_collection_ids(ident.principals):
        raise HTTPException(404, "collection not found")        # don't reveal what exists


def _require_write(ident: Identity, collection_id: str) -> None:
    c = _collection(collection_id)
    member = c.access == "group" and bool(set(c.group_ids or []) & set(ident.groups))
    if not (ident.is_admin or c.owner_id == ident.user_id or member):
        raise HTTPException(403, "only the owner, a member of the collection's group or an admin can change it")


@router.get("/collections")
def list_collections(ident: Identity = Depends(current_user)) -> list[dict[str, Any]]:
    readable = readable_collection_ids(ident.principals)
    with db.session() as s:
        cols = s.scalars(select(CollectionRow).where(CollectionRow.id.in_(readable)).order_by(CollectionRow.name)).all()
        docs = dict(s.execute(select(DocumentRow.collection_id, func.count()).where(DocumentRow.deleted.is_(False))
                              .group_by(DocumentRow.collection_id)).all())
        chunks = dict(s.execute(select(ChunkRow.collection_id, func.sum(ChunkRow.token_estimate))
                                .group_by(ChunkRow.collection_id)).all())
        out = [ingest.collection_dict(c) | {"documents": int(docs.get(c.id, 0)), "tokens": int(chunks.get(c.id, 0) or 0),
                                            "acl": collection_acl(c.access, c.owner_id, list(c.group_ids or []))}
               for c in cols]
    usage = {r["collection_id"]: r for r in _collection_usage()}
    for c in out:
        c["usage"] = usage.get(c["id"])
    return out


def _collection_usage() -> list[dict[str, Any]]:
    from ..ledger import ledger

    return ledger().by_collection()


@router.post("/collections")
def create_collection(body: CollectionIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    try:
        return ingest.create_collection(body.name, ident.user_id, body.access, body.group_ids)
    except ingest.IngestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/collections/{collection_id}")
def patch_collection(collection_id: str, body: CollectionPatch, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _require_write(ident, collection_id)
    try:
        return ingest.update_collection(collection_id, body.name, body.access, body.group_ids)
    except ingest.IngestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/documents/upload-url")
def upload_url(body: UploadUrlIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _require_write(ident, body.collection_id)
    safe = "".join(ch for ch in body.filename if ch.isalnum() or ch in "._- ")[:200] or "upload"
    return blobstore.upload_url(f"kb/{body.collection_id}/uploads/{uuid.uuid4().hex}/{safe}")


@router.put("/uploads")
async def local_upload(request: Request, key: str, exp: int, sig: str) -> dict[str, Any]:
    """Local stand-in for an S3 presigned PUT (only when no blob bucket is configured)."""
    if settings.blob_bucket or not blobstore.check_signature(key, exp, sig):
        raise HTTPException(403, "invalid or expired upload URL")
    data = await request.body()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    blobstore.put(key, data)
    return {"key": key, "bytes": len(data)}


@router.post("/documents")
def register_document(body: DocumentIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    _require_write(ident, body.collection_id)
    if not body.key.startswith(f"kb/{body.collection_id}/"):
        raise HTTPException(422, "upload key does not belong to this collection")
    try:
        return ingest.register(body.collection_id, body.title, body.filename, key=body.key, created_by=ident.user_id)
    except ingest.IngestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/documents")
def list_documents(collection_id: str, ident: Identity = Depends(current_user)) -> list[dict[str, Any]]:
    _require_read(ident, collection_id)
    return ingest.list_documents(collection_id)


@router.get("/documents/{document_id}")
def get_document(document_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    try:
        d = ingest.get_document(document_id)
    except ingest.IngestError as exc:
        raise HTTPException(404, str(exc)) from exc
    _require_read(ident, d["collection_id"])
    return d


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: str, ident: Identity = Depends(current_user)) -> list[dict[str, Any]]:
    d = get_document(document_id, ident)
    _require_read(ident, d["collection_id"])
    return ingest.document_chunks(document_id)


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, ident: Identity = Depends(current_user)) -> dict[str, str]:
    d = get_document(document_id, ident)
    _require_write(ident, d["collection_id"])
    ingest.delete_document(document_id)
    return {"status": "deleted"}


@router.post("/documents/{document_id}/reindex")
def reindex_document(document_id: str, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    d = get_document(document_id, ident)
    _require_write(ident, d["collection_id"])
    return ingest.reindex(document_id)


@router.post("/search")
def search(body: SearchIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    return service.search(body.query, ident.user_id, body.collection_ids, body.k)


@router.get("/rag/metrics")
def rag_metrics() -> dict[str, Any]:
    """Retrieval latency, relevance-gate rate, average context tokens, knowledge cache hit rate, citation rate."""
    L = LedgerRow
    with db.session() as s:
        is_knowledge = L.context_fingerprint.is_not(None)
        n, hits, llm, gated, ctx_tokens, uncited = s.execute(select(
            func.count(), func.sum(case((L.route == "cache", 1), else_=0)),
            func.sum(case((L.route == "llm", 1), else_=0)),
            func.sum(case(((L.route == "llm") & (L.context_tokens == 0), 1), else_=0)),
            func.sum(case((L.route == "llm", L.context_tokens), else_=0)),
            func.sum(case(((L.route == "llm") & (L.context_tokens > 0) & L.uncited.is_(True), 1), else_=0)),
        ).where(is_knowledge)).one()
    n, hits, llm, gated, ctx_tokens, uncited = (int(x or 0) for x in (n, hits, llm, gated, ctx_tokens, uncited))
    with_context = llm - gated
    return {
        "knowledge_requests": n,
        "retrieval_latency_ms": service.retrieval_latency(),
        "relevance_gate_rate": round(gated / llm, 4) if llm else 0.0,
        "avg_context_tokens": round(ctx_tokens / with_context, 1) if with_context else 0.0,
        "knowledge_cache_hit_rate": round(hits / n, 4) if n else 0.0,
        "citation_rate": round((with_context - uncited) / with_context, 4) if with_context else 0.0,
    }


@router.get("/users/{user_id}/groups")
def get_groups(user_id: str) -> dict[str, Any]:
    return {"user_id": user_id, "groups": store().groups_of(user_id)}


@router.put("/users/{user_id}/groups")
def put_groups(user_id: str, body: GroupsIn, ident: Identity = Depends(current_user)) -> dict[str, Any]:
    if not ident.is_admin:
        raise HTTPException(403, "admin role required")
    store().set_groups(user_id, body.groups)
    return {"user_id": user_id, "groups": store().groups_of(user_id)}
