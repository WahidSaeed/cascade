"""Automatic re-verification of document-backed artifacts (BUILD.md §9.5, rule 13).

A kb_event on a dependency (or a new document in a dependency's collection) queues
re-verification against the RAG pipeline on the artifact's holdout set; if agreement falls below
the threshold the artifact is demoted to `shadow` with an alert. The cost is `reverify` overhead.
"""
from __future__ import annotations

import logging

from sqlalchemy import update

from . import db, hooks
from .models import KbEvent
from .schemas import PatternStatus
from .store import store

log = logging.getLogger("cascade.reverify")


@hooks.on("kb_event")
def on_kb_event(collection_id: str, document_id: str, kind: str) -> list[str]:
    from .lookup import reverify_lookup

    checked = []
    for p in store().list_patterns():
        if not p.kb_dependencies or p.status not in (PatternStatus.approved, PatternStatus.draft):
            continue
        dep_docs = {d.document_id for d in p.kb_dependencies}
        dep_cols = {d.collection_id for d in p.kb_dependencies}
        if document_id in dep_docs or collection_id in dep_cols:
            reason = f"document {kind} in a dependency" if document_id in dep_docs else \
                f"new or changed document in a dependency collection ({kind})"
            reverify_lookup(p, reason)
            checked.append(p.id)
    with db.session() as s:
        s.execute(update(KbEvent).where(KbEvent.document_id == document_id, KbEvent.processed.is_(False))
                  .values(processed=True))
    return checked
