"""Ingestion (BUILD.md §9.2): register -> (queue) -> parse -> chunk -> embed -> atomic version swap.

  - Uploads land at kb/<collection>/<document>/<version>/<filename> (S3 or local blobs).
  - Idempotent on (document, sha256): re-registering identical bytes creates nothing new.
  - A changed file becomes a *pending* version; the current version keeps serving until the new
    chunks are embedded, then one transaction inserts the new chunks, flips the document's version,
    deletes the old chunks and writes a kb_event.
  - Queue: SQS + the `cascade-ingest` worker in AWS (CASCADE_INGEST_QUEUE_URL); an in-process
    background thread locally.
  - Embedding tokens are recorded as `ingestion` overhead when a paid embedder (Titan) is configured.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import delete, select, update

from .. import blobstore, db, hooks, reverify  # noqa: F401 - registers the kb_event re-verification hook
from ..config import settings
from ..light.embedder import get_embedder
from ..models import ChunkRow, CollectionRow, DocumentRow, KbEvent, new_id
from ..pricing import embed_cost
from .access import collection_acl
from .chunk import chunk_blocks
from .parse import ParseError, detect_mime, parse

log = logging.getLogger("cascade.ingest")
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
_ingest_lock = threading.Lock()


class IngestError(ValueError):
    pass


# ----------------------------------------------------------------------------- collections
def create_collection(name: str, owner_id: str, access: str, group_ids: list[str] | None = None,
                      collection_id: str | None = None) -> dict[str, Any]:
    if access not in ("org", "group", "private"):
        raise IngestError("access must be org, group or private")
    if access == "group" and not group_ids:
        raise IngestError("group access needs at least one group")
    with db.session() as s:
        c = CollectionRow(id=collection_id or new_id("col"), name=name, owner_id=owner_id, access=access,
                          group_ids=list(group_ids or []), created_at=time.time())
        s.add(c)
    return collection_dict(c)


def update_collection(collection_id: str, name: str | None = None, access: str | None = None,
                      group_ids: list[str] | None = None) -> dict[str, Any]:
    """Changing access rewrites every chunk's ACL in the same transaction (§9.1)."""
    with db.session() as s:
        c = s.get(CollectionRow, collection_id, with_for_update=True)
        if c is None:
            raise IngestError("collection not found")
        if name:
            c.name = name
        if access:
            if access not in ("org", "group", "private"):
                raise IngestError("access must be org, group or private")
            c.access = access
        if group_ids is not None:
            c.group_ids = list(group_ids)
        acl = collection_acl(c.access, c.owner_id, list(c.group_ids or []))
        s.execute(update(ChunkRow).where(ChunkRow.collection_id == collection_id).values(acl=acl))
        return collection_dict(c)


def collection_dict(c: CollectionRow) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "owner_id": c.owner_id, "access": c.access,
            "group_ids": list(c.group_ids or []), "created_at": c.created_at}


# ----------------------------------------------------------------------------- documents
def blob_key(collection_id: str, document_id: str, version: int, filename: str) -> str:
    return f"kb/{collection_id}/{document_id}/{version}/{filename}"


def register(collection_id: str, title: str, filename: str, data: bytes | None = None, key: str | None = None,
             created_by: str = "", sync: bool = False, document_id: str | None = None) -> dict[str, Any]:
    """Register an uploaded file (bytes, or a blob key the client uploaded to) and queue ingestion.

    A document is identified by (collection, title): a re-upload with different bytes is a new version.
    """
    if data is None:
        if not key or not blobstore.exists(key):
            raise IngestError("upload not found")
        data = blobstore.get(key)
    if not data:
        raise IngestError("empty file")
    digest = blobstore.sha256(data)
    mime = detect_mime(filename, data)
    with db.session() as s:
        if s.get(CollectionRow, collection_id) is None:
            raise IngestError("collection not found")
        doc = s.get(DocumentRow, document_id) if document_id else s.scalars(
            select(DocumentRow).where(DocumentRow.collection_id == collection_id, DocumentRow.title == title,
                                      DocumentRow.deleted.is_(False))).first()
        if doc is not None and digest in (doc.sha256, doc.pending_sha256):
            return {**document_dict(doc), "unchanged": True}          # idempotent on (document, sha256)
        if doc is None:
            doc = DocumentRow(id=new_id("doc"), collection_id=collection_id, title=title, source_uri="", mime=mime,
                              version=0, sha256="", status="queued", error="", created_by=created_by,
                              updated_at=time.time(), deleted=False, filename=filename)
            s.add(doc)
            s.flush()
        version = max(doc.version, doc.pending_version or 0) + 1   # every upload gets its own version number
        uri = blobstore.put(blob_key(collection_id, doc.id, version, filename), data)
        doc.pending_version, doc.pending_status, doc.pending_sha256 = version, "queued", digest
        doc.mime, doc.filename, doc.updated_at = mime, filename, time.time()
        if doc.version == 0:
            doc.status = "queued"
        doc.source_uri = uri
        doc_id = doc.id
    enqueue(doc_id, sync=sync)
    return get_document(doc_id)


def enqueue(document_id: str, sync: bool = False) -> None:
    if sync:
        ingest(document_id)
    elif settings.ingest_queue_url:
        import boto3

        boto3.client("sqs", region_name=settings.bedrock_region).send_message(
            QueueUrl=settings.ingest_queue_url, MessageBody=json.dumps({"document_id": document_id}))
    else:
        _pool.submit(_safe_ingest, document_id)


def _safe_ingest(document_id: str) -> None:
    try:
        ingest(document_id)
    except Exception:  # noqa: BLE001 - recorded on the document
        log.exception("ingestion failed for %s", document_id)


def _set_status(document_id: str, status: str, error: str = "") -> None:
    with db.session() as s:
        d = s.get(DocumentRow, document_id)
        if d is None:
            return
        d.pending_status = status
        if d.version == 0:                      # first version: the document's own status
            d.status = status
        if error:
            d.error = error
        d.updated_at = time.time()


def ingest(document_id: str) -> dict[str, Any]:
    with _ingest_lock:
        with db.session() as s:
            d = s.get(DocumentRow, document_id)
            if d is None or d.deleted:
                raise IngestError("document not found")
            version = d.pending_version or d.version
            key = blob_key(d.collection_id, d.id, version, d.filename)
            collection_id, title, mime, old_version, reindex = d.collection_id, d.title, d.mime, d.version, \
                d.pending_version is None
            new_sha = d.pending_sha256 or d.sha256
        try:
            _set_status(document_id, "parsing")
            chunks = chunk_blocks(parse(blobstore.get(key), mime, title), settings.chunk_tokens, settings.chunk_overlap)
            if not chunks:
                raise ParseError("document has no text")
            _set_status(document_id, "embedding")
            emb = get_embedder()
            vectors = emb.embed_many([f"{c.heading_path} {c.text}" for c in chunks])
            if emb.paid:
                from ..ledger import OverheadEntry, ledger

                tokens = sum(len(c.text) // 4 for c in chunks)
                ledger().record_overhead(OverheadEntry(category="ingestion", input_tokens=tokens, model_id=emb.model_id,
                                                       cost_usd=embed_cost(emb.model_id, tokens),
                                                       collection_id=collection_id, note=title))
        except Exception as exc:
            _set_status(document_id, "failed", str(exc)[:500])
            raise
        # atomic swap: new chunks in, version flipped, old chunks out, event written — one transaction.
        # Compare-and-set under row locks: a newer upload or another worker always wins, and the ACL is
        # recomputed here so a concurrent access change can never leave new chunks with a stale ACL.
        with db.session() as s:
            d = s.get(DocumentRow, document_id, with_for_update=True)
            assert d is not None
            col = s.get(CollectionRow, collection_id, with_for_update=True)
            assert col is not None
            stale = (d.deleted or (reindex and (d.pending_version is not None or d.version != version))
                     or (not reindex and d.pending_version != version) or d.version > version)
            if stale:
                log.info("ingestion of %s v%s superseded; discarded", document_id, version)
                return document_dict(d)
            acl = collection_acl(col.access, col.owner_id, list(col.group_ids or []))
            if reindex:
                s.execute(delete(ChunkRow).where(ChunkRow.document_id == document_id))
            for c, v in zip(chunks, vectors, strict=True):
                s.add(ChunkRow(id=new_id("chk"), document_id=document_id, collection_id=collection_id,
                               doc_version=version, ord=c.ord, text=c.text, heading_path=c.heading_path, page=c.page,
                               token_estimate=c.token_estimate, embedding=v, acl=acl))
            s.flush()
            d.version, d.sha256, d.status, d.error = version, new_sha, "ready", ""
            d.pending_version = d.pending_status = d.pending_sha256 = None
            d.updated_at = time.time()
            if not reindex:
                s.execute(delete(ChunkRow).where(ChunkRow.document_id == document_id, ChunkRow.doc_version != version))
            kind = "added" if old_version == 0 else "updated"
            s.add(KbEvent(collection_id=collection_id, document_id=document_id, kind=kind, at=time.time(),
                          processed=False))
        if not reindex:
            hooks.fire("kb_event", collection_id=collection_id, document_id=document_id, kind=kind)
        return get_document(document_id)


def delete_document(document_id: str) -> None:
    # waits for an in-flight ingestion of the same document instead of racing its transaction
    with _ingest_lock, db.session() as s:
        d = s.get(DocumentRow, document_id)
        if d is None:
            raise IngestError("document not found")
        d.deleted = True
        d.updated_at = time.time()
        s.execute(delete(ChunkRow).where(ChunkRow.document_id == document_id))
        s.add(KbEvent(collection_id=d.collection_id, document_id=document_id, kind="deleted", at=time.time(),
                      processed=False))
        collection_id = d.collection_id
    hooks.fire("kb_event", collection_id=collection_id, document_id=document_id, kind="deleted")


def reindex(document_id: str, sync: bool = False) -> dict[str, Any]:
    with db.session() as s:
        d = s.get(DocumentRow, document_id)
        if d is None or d.deleted:
            raise IngestError("document not found")
    enqueue(document_id, sync=sync)
    return get_document(document_id)


def document_dict(d: DocumentRow) -> dict[str, Any]:
    return {"id": d.id, "collection_id": d.collection_id, "title": d.title, "filename": d.filename, "mime": d.mime,
            "version": d.version, "sha256": d.sha256, "status": d.status, "error": d.error,
            "pending_version": d.pending_version, "pending_status": d.pending_status,
            "created_by": d.created_by, "updated_at": d.updated_at, "source_uri": d.source_uri}


def get_document(document_id: str) -> dict[str, Any]:
    with db.session() as s:
        d = s.get(DocumentRow, document_id)
        if d is None:
            raise IngestError("document not found")
        out = document_dict(d)
        out["chunks"] = len(list(s.scalars(select(ChunkRow.id).where(ChunkRow.document_id == document_id))))
        return out


def list_documents(collection_id: str) -> list[dict[str, Any]]:
    with db.session() as s:
        docs = s.scalars(select(DocumentRow).where(DocumentRow.collection_id == collection_id,
                                                   DocumentRow.deleted.is_(False))
                         .order_by(DocumentRow.title)).all()
        return [document_dict(d) for d in docs]


def document_chunks(document_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(select(ChunkRow).where(ChunkRow.document_id == document_id).order_by(ChunkRow.ord)
                         .limit(limit)).all()
        return [{"id": c.id, "ord": c.ord, "version": c.doc_version, "heading_path": c.heading_path, "page": c.page,
                 "token_estimate": c.token_estimate, "text": c.text} for c in rows]
