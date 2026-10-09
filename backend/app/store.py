"""SqlStore: the single persistence interface (SQLite or PostgreSQL via SQLAlchemy 2).

Domain modules (cache, ledger, rag, agent) use `db.session()` for their own tables; this
class owns patterns, users, conversations and runtime configuration.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select

from . import db
from .models import ConfigRow, Conversation, PatternRow, Turn, User, UserGroup
from .schemas import Pattern, PatternStatus


class SqlStore:
    # ------------------------------------------------------------------ patterns
    def list_patterns(self, status: str | None = None) -> list[Pattern]:
        with db.session() as s:
            q = select(PatternRow)
            if status:
                q = q.where(PatternRow.status == status)
            rows = s.scalars(q).all()
            pats = [Pattern.model_validate(r.data) for r in rows]
        return sorted(pats, key=lambda p: p.created_at, reverse=True)

    def get_pattern(self, pattern_id: str) -> Pattern | None:
        with db.session() as s:
            row = s.get(PatternRow, pattern_id)
            return Pattern.model_validate(row.data) if row else None

    def put_pattern(self, pattern: Pattern) -> Pattern:
        pattern.updated_at = time.time()
        data = pattern.model_dump(mode="json")
        with db.session() as s:
            row = s.get(PatternRow, pattern.id)
            if row is None:
                row = PatternRow(id=pattern.id, created_at=pattern.created_at)
                s.add(row)
            row.name = pattern.name
            row.status = pattern.status.value
            row.task_type = pattern.task_type.value if pattern.task_type else None
            row.kind = pattern.kind.value if pattern.kind else None
            row.data = data
            row.updated_at = pattern.updated_at
        return pattern

    def approved_patterns(self) -> list[Pattern]:
        return self.list_patterns(PatternStatus.approved.value)

    def delete_pattern(self, pattern_id: str) -> None:
        with db.session() as s:
            s.execute(delete(PatternRow).where(PatternRow.id == pattern_id))

    # ------------------------------------------------------------------ users
    def upsert_user(self, user_id: str, name: str = "", role: str = "", is_admin: bool = False,
                    persona: bool = False, groups: list[str] | None = None) -> None:
        with db.session() as s:
            u = s.get(User, user_id)
            if u is None:
                u = User(id=user_id)
                s.add(u)
            u.name, u.role, u.is_admin, u.persona = name or user_id, role, is_admin, persona
            if groups is not None:
                s.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
                for g in groups:
                    s.add(UserGroup(user_id=user_id, group_id=g))

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with db.session() as s:
            u = s.get(User, user_id)
            if u is None:
                return None
            groups = list(s.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user_id)))
            return {"id": u.id, "name": u.name, "role": u.role, "is_admin": u.is_admin,
                    "persona": u.persona, "groups": groups}

    def list_users(self, personas_only: bool = False) -> list[dict[str, Any]]:
        with db.session() as s:
            q = select(User.id)
            if personas_only:
                q = q.where(User.persona.is_(True))
            ids = list(s.scalars(q.order_by(User.id)))
        return [u for u in (self.get_user(i) for i in ids) if u]

    def groups_of(self, user_id: str) -> list[str]:
        with db.session() as s:
            return list(s.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user_id)))

    def set_groups(self, user_id: str, groups: list[str]) -> None:
        with db.session() as s:
            if s.get(User, user_id) is None:
                s.add(User(id=user_id, name=user_id))
            s.execute(delete(UserGroup).where(UserGroup.user_id == user_id))
            for g in groups:
                s.add(UserGroup(user_id=user_id, group_id=g))

    def principals(self, user_id: str, groups: list[str] | None = None) -> list[str]:
        """Knowledge principals (BUILD.md §4.4): user:<id>, group:<g> for each group, org:*."""
        gs = self.groups_of(user_id) if groups is None else groups
        return [f"user:{user_id}", *[f"group:{g}" for g in sorted(gs)], "org:*"]

    # ------------------------------------------------------------------ conversations
    def get_or_create_conversation(self, conversation_id: str | None, user_id: str,
                                   collection_ids: list[str] | None = None) -> tuple[str, list[str]]:
        with db.session() as s:
            conv = s.get(Conversation, conversation_id) if conversation_id else None
            if conv is None or conv.user_id != user_id:
                conv = Conversation(user_id=user_id, collection_ids=list(collection_ids or []))
                if conversation_id and s.get(Conversation, conversation_id) is None:
                    conv.id = conversation_id
                s.add(conv)
                s.flush()
            elif collection_ids is not None and list(collection_ids) != list(conv.collection_ids or []):
                conv.collection_ids = list(collection_ids)
            return conv.id, list(conv.collection_ids or [])

    def add_turn(self, conversation_id: str, user_id: str, role: str, text: str,
                 request_id: str | None = None, ts: float | None = None) -> None:
        from .config import settings

        t = ts or time.time()
        with db.session() as s:
            s.add(Turn(conversation_id=conversation_id, user_id=user_id, role=role, text=text,
                       request_id=request_id, ts=t, expires_at=t + settings.log_retention_days * 86400))

    def recent_turns(self, conversation_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with db.session() as s:
            rows = s.scalars(select(Turn).where(Turn.conversation_id == conversation_id)
                             .order_by(Turn.id.desc()).limit(limit)).all()
            return [{"role": r.role, "text": r.text, "ts": r.ts} for r in reversed(rows)]

    # ------------------------------------------------------------------ runtime config
    def config_overrides(self) -> dict[str, Any]:
        with db.session() as s:
            return {r.key: r.value for r in s.scalars(select(ConfigRow))}

    def set_config(self, key: str, value: Any) -> None:
        with db.session() as s:
            row = s.get(ConfigRow, key)
            if row is None:
                s.add(ConfigRow(key=key, value=value))
            else:
                row.value = value


_store: SqlStore | None = None


def store() -> SqlStore:
    global _store
    if _store is None:
        _store = SqlStore()
    return _store
