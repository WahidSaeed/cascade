"""Identity (BUILD.md §4.4, §12).

  CASCADE_AUTH=local    the client sends X-User-Id (persona switcher); development and the demo
  CASCADE_AUTH=cognito  identity comes from the Cognito JWT (Phase 6)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from .config import settings
from .store import store


@dataclass
class Identity:
    user_id: str
    groups: list[str] = field(default_factory=list)
    is_admin: bool = False

    @property
    def principals(self) -> list[str]:
        return store().principals(self.user_id, self.groups)


def current_user(request: Request) -> Identity:
    if settings.auth == "cognito":
        from .cognito import identity_from_request

        ident: Identity = identity_from_request(request)
        return ident
    user_id = (request.headers.get("X-User-Id") or "anonymous").strip()[:64] or "anonymous"
    u = store().get_user(user_id)
    groups = list(u["groups"]) if u else []
    return Identity(user_id=user_id, groups=groups, is_admin=bool(u and u["is_admin"]) or "admins" in groups)


def require_admin(request: Request) -> Identity:
    ident = current_user(request)
    if settings.auth == "cognito" and not ident.is_admin:
        raise HTTPException(403, "admin role required")
    return ident
