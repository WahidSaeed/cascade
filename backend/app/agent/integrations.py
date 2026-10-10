"""Integrations with existing systems (BUILD.md §11.2 question 3, rule 11).

Registered at intake (HTTP endpoint, AWS resource by ARN, or an uploaded Python module), each with a
role (feature source, pre/post-processing, core model). Credentials go to Secrets Manager
(`cascade/integrations/<id>`; a local file in development). Nothing can call an integration until an
admin approves it, and calls may only reach the registered target. Modules are scanned for the reviewer.
"""
from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .. import db
from ..config import settings
from ..models import IntegrationRow, new_id

TYPES = ("http", "aws", "module")
ROLES = ("feature_source", "pre_post", "core_model")
RISKY_IMPORTS = ("subprocess", "socket", "os.system", "ctypes", "pickle", "eval(", "exec(", "requests", "urllib")


class IntegrationError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Egress only to the registered target: a redirect elsewhere is refused, never followed."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise IntegrationError("the integration answered with a redirect; only the registered target may be called")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _secrets_file() -> Path:
    p = Path(settings.data_dir) / "secrets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def store_secret(integration_id: str, value: str) -> str:
    name = f"cascade/integrations/{integration_id}"
    if settings.env == "prod":
        import boto3

        arn = boto3.client("secretsmanager", region_name=settings.bedrock_region).create_secret(
            Name=name, SecretString=value)["ARN"]
        return str(arn)
    f = _secrets_file()
    data = json.loads(f.read_text()) if f.exists() else {}
    data[name] = value
    f.write_text(json.dumps(data))
    f.chmod(0o600)
    return f"local:{name}"


def _secret(arn: str) -> str | None:
    if not arn:
        return None
    if arn.startswith("local:"):
        data = json.loads(_secrets_file().read_text())
        return str(data.get(arn.removeprefix("local:")))
    import boto3

    return str(boto3.client("secretsmanager", region_name=settings.bedrock_region).get_secret_value(SecretId=arn)
               ["SecretString"])


def scan_module(data: bytes) -> dict[str, Any]:
    """Dependency + risky-call scan of an uploaded wheel/zip, shown to the reviewer."""
    findings: list[str] = []
    deps: list[str] = []
    files: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            files.append(n)
            if n.endswith(("requirements.txt", "METADATA")):
                txt = z.read(n).decode("utf-8", "replace")
                deps += [ln.split(":", 1)[-1].strip() for ln in txt.splitlines()
                         if ln.strip() and (n.endswith(".txt") or ln.startswith("Requires-Dist"))]
            if n.endswith(".py"):
                src = z.read(n).decode("utf-8", "replace")
                findings += [f"{n}: uses {r.rstrip('(')}" for r in RISKY_IMPORTS if r in src]
    return {"files": files[:200], "dependencies": deps, "findings": findings}


def register(owner: str, name: str, type_: str, target: str, role: str, config: dict[str, Any] | None = None,
             secret: str | None = None, module: bytes | None = None) -> dict[str, Any]:
    if type_ not in TYPES or role not in ROLES:
        raise IntegrationError(f"type must be one of {TYPES}, role one of {ROLES}")
    if type_ == "http" and urllib.parse.urlparse(target).scheme not in ("http", "https"):
        raise IntegrationError("HTTP integrations need an http(s) URL")
    if type_ == "aws" and not target.startswith("arn:aws:"):
        raise IntegrationError("AWS integrations are referenced by ARN")
    iid = new_id("int")
    scan = scan_module(module) if module else {}
    arn = store_secret(iid, secret) if secret else ""
    with db.session() as s:
        s.add(IntegrationRow(id=iid, name=name, type=type_, target=target, role=role, config=config or {}, secret_arn=arn,
                             status="pending", approved_by=None, owner=owner, scan=scan, created_at=time.time()))
    return get(iid)


def get(integration_id: str) -> dict[str, Any]:
    with db.session() as s:
        r = s.get(IntegrationRow, integration_id)
        if r is None:
            raise IntegrationError("integration not found")
        return {"id": r.id, "name": r.name, "type": r.type, "target": r.target, "role": r.role, "config": r.config,
                "status": r.status, "approved_by": r.approved_by, "owner": r.owner, "scan": r.scan,
                "has_secret": bool(r.secret_arn), "created_at": r.created_at}


def list_all(status: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    with db.session() as s:
        ids = list(s.scalars(select(IntegrationRow.id).order_by(IntegrationRow.created_at.desc())))
    return [i for i in (get(x) for x in ids) if status is None or i["status"] == status]


def decide(integration_id: str, admin: str, approve: bool) -> dict[str, Any]:
    with db.session() as s:
        r = s.get(IntegrationRow, integration_id)
        if r is None:
            raise IntegrationError("integration not found")
        r.status = "approved" if approve else "rejected"
        r.approved_by = admin
    if not approve:
        from .. import patterns as pat
        from ..schemas import PatternStatus
        from ..store import store

        for p in store().list_patterns(PatternStatus.approved.value):
            if integration_id in p.integration_ids:
                pat.demote(p.id, f"Integration {get(integration_id)['name']!r} was rejected by {admin}; the artifact "
                                 "no longer serves")
    return get(integration_id)


def call(integration_id: str, payload: dict[str, Any], timeout_s: float = 3.0) -> dict[str, Any]:
    """Call an APPROVED integration at its registered target only."""
    i = get(integration_id)
    if i["status"] != "approved":
        raise IntegrationError(f"integration {i['name']!r} is {i['status']}: an admin must approve it first")
    if i["type"] != "http":
        raise IntegrationError("only HTTP integrations can be called from the agent in this build")
    cfg = i["config"]
    url = i["target"]
    method = str(cfg.get("method", "GET")).upper()
    headers = {"Accept": "application/json"}
    with db.session() as s:
        row = s.get(IntegrationRow, integration_id)
        secret = _secret(row.secret_arn) if row else None
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    if method == "GET":
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(payload)
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 - registered, approved target only
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method=method,  # noqa: S310
                                     headers={**headers, "Content-Type": "application/json"})
    if urllib.parse.urlparse(req.full_url).netloc != urllib.parse.urlparse(i["target"]).netloc:
        raise IntegrationError("egress outside the registered target is not allowed")
    with _OPENER.open(req, timeout=timeout_s) as resp:
        return dict(json.loads(resp.read().decode()))
