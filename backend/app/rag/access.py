"""Knowledge access control (BUILD.md §4.4, rule 13).

A collection's ACL is a list of principals: `org:*` (everyone), `group:<id>`, `user:<owner>`.
Chunks carry a copy of it (`chunks.acl`), so retrieval filters inside the SQL query.
"""
from __future__ import annotations

from sqlalchemy import select

from .. import db
from ..models import CollectionRow, DocumentRow


def collection_acl(access: str, owner_id: str, group_ids: list[str]) -> list[str]:
    if access == "org":
        return ["org:*"]
    if access == "group":
        return sorted({f"group:{g}" for g in group_ids} | {f"user:{owner_id}"})
    return [f"user:{owner_id}"]


def readable_collection_ids(principals: list[str]) -> set[str]:
    with db.session() as s:
        rows = s.execute(select(CollectionRow.id, CollectionRow.access, CollectionRow.owner_id,
                                CollectionRow.group_ids)).all()
    p = set(principals)
    return {cid for cid, access, owner, groups in rows if set(collection_acl(access, owner, list(groups or []))) & p}


def can_read_documents(document_ids: list[str], principals: list[str]) -> bool:
    if not document_ids:
        return True
    readable = readable_collection_ids(principals)
    with db.session() as s:
        rows = s.execute(select(DocumentRow.id, DocumentRow.collection_id)
                         .where(DocumentRow.id.in_(document_ids))).all()
    found = {d: c for d, c in rows}
    return all(found.get(d) in readable for d in document_ids)


def current_versions(document_ids: list[str]) -> dict[str, int]:
    with db.session() as s:
        rows = s.execute(select(DocumentRow.id, DocumentRow.version)
                         .where(DocumentRow.id.in_(document_ids), DocumentRow.deleted.is_(False),
                                DocumentRow.status == "ready")).all()
    return {d: int(v) for d, v in rows}
