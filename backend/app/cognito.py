"""Cognito authentication (BUILD.md §12, CASCADE_AUTH=cognito).

Identity comes from the Cognito JWT (RS256, verified against the user pool's JWKS: signature, issuer,
audience/client id, expiry). `cognito:groups` become knowledge principals (`group:<g>`), and the admin
group grants admin actions. Every /api/* request except /api/health needs a valid token (middleware).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any

import jwt
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import settings

PUBLIC_PATHS = {"/api/health"}
_lock = threading.Lock()
_jwks: dict[str, Any] = {"keys": None, "fetched": 0.0}
JWKS_TTL_S = 3600


def _fetch_jwks() -> dict[str, Any]:
    url = settings.cognito_jwks_url or f"{settings.cognito_issuer.rstrip('/')}/.well-known/jwks.json"
    if url.startswith("file://"):                               # tests: a local JWKS fixture
        with open(url.removeprefix("file://")) as fh:
            return dict(json.load(fh))
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - the configured user pool only
        return dict(json.loads(resp.read().decode()))


def signing_key(kid: str) -> Any:
    with _lock:
        if _jwks["keys"] is None or time.time() - _jwks["fetched"] > JWKS_TTL_S \
                or not any(k.get("kid") == kid for k in _jwks["keys"]):
            _jwks["keys"] = _fetch_jwks().get("keys", [])
            _jwks["fetched"] = time.time()
        for k in _jwks["keys"]:
            if k.get("kid") == kid:
                return jwt.PyJWK(k).key
    raise HTTPException(401, "unknown signing key")


def reset_jwks() -> None:
    with _lock:
        _jwks["keys"] = None


def verify_token(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        claims: dict[str, Any] = jwt.decode(
            token, signing_key(str(header.get("kid", ""))), algorithms=["RS256"], issuer=settings.cognito_issuer,
            options={"require": ["exp", "iss", "sub"], "verify_aud": False})
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    # ID tokens carry `aud`, access tokens carry `client_id`: either must be this app's client
    audience = claims.get("aud") or claims.get("client_id")
    if not settings.cognito_audience:
        raise HTTPException(401, "CASCADE_COGNITO_AUDIENCE is not configured: refusing all tokens")
    if audience != settings.cognito_audience:
        raise HTTPException(401, "token was issued for another client")
    if claims.get("token_use") not in (None, "id", "access"):
        raise HTTPException(401, "unexpected token use")
    return claims


def bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return auth.split(" ", 1)[1].strip()


def identity_from_request(request: Request) -> Any:
    from .auth import Identity

    claims = getattr(request.state, "claims", None) or verify_token(bearer(request))
    user_id = str(claims.get("cognito:username") or claims.get("username") or claims["sub"])
    groups = [str(g) for g in claims.get("cognito:groups", [])]
    return Identity(user_id=user_id, groups=groups, is_admin=settings.admin_group in groups)


class CognitoMiddleware(BaseHTTPMiddleware):
    """401 for every /api/* call without a valid JWT (except /api/health) when CASCADE_AUTH=cognito."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if settings.auth == "cognito" and path.startswith("/api/") and path not in PUBLIC_PATHS \
                and request.method != "OPTIONS":
            try:
                request.state.claims = verify_token(bearer(request))
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                    headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)
