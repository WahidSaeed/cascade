"""Phase 6 acceptance (BUILD.md §12): auth, static export + CORS, retention, IaC + CI, DEPLOY.md."""
import importlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import jwt
import pytest
import time_machine
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import cognito, db, engine, retention, seed
from app.config import settings
from app.models import CacheEntry, Conversation, LedgerRow, OverheadRow, PatternRow, RequestLog, Turn
from tests.helpers import install

ROOT = Path(__file__).resolve().parents[3]
ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
CLIENT = "cascade-web-client"


@pytest.fixture
def issuer(tmp_path, monkeypatch):
    """A local JWT issuer: a test RSA key and its JWKS (the Cognito stand-in, §16.4)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    (tmp_path / "jwks.json").write_text(json.dumps({"keys": [jwk]}))
    monkeypatch.setattr(settings, "auth", "cognito")
    monkeypatch.setattr(settings, "cognito_issuer", ISSUER)
    monkeypatch.setattr(settings, "cognito_audience", CLIENT)
    monkeypatch.setattr(settings, "cognito_jwks_url", f"file://{tmp_path / 'jwks.json'}")
    cognito.reset_jwks()

    def token(user="ben", groups=("support", "managers"), exp=3600, aud=CLIENT, signer=key, iss=ISSUER):
        now = int(time.time())
        claims = {"sub": f"sub-{user}", "cognito:username": user, "cognito:groups": list(groups), "iss": iss,
                  "aud": aud, "token_use": "id", "iat": now, "exp": now + exp}
        return jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "test-key"})
    yield token
    cognito.reset_jwks()


@pytest.mark.acceptance("P6-01")
def test_cognito_jwt_required_groups_and_admin(client, issuer):
    assert client.get("/api/health").status_code == 200                                   # public
    for path in ("/api/patterns", "/api/ledger/summary", "/api/me", "/api/collections"):
        assert client.get(path).status_code == 401                                         # no token
    assert client.post("/api/chat", json={"prompt": "hi"}).status_code == 401
    bad_sig = issuer(signer=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    for t in (issuer(exp=-10), issuer(aud="another-client"), bad_sig, issuer(iss="https://evil.example.com")):
        assert client.get("/api/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401
    ben = {"Authorization": f"Bearer {issuer()}"}
    me = client.get("/api/me", headers=ben).json()
    assert me["user_id"] == "ben" and me["principals"] == ["user:ben", "group:managers", "group:support", "org:*"]
    assert not me["is_admin"]
    p = install("conversion")
    assert client.post(f"/api/patterns/{p.id}/reject", headers=ben).status_code == 403    # non-admin
    assert client.put("/api/config", json={"values": {"cache_threshold": 0.9}}, headers=ben).status_code == 403
    assert client.put("/api/users/dana/groups", json={"groups": ["hr"]}, headers=ben).status_code == 403
    eli = {"Authorization": f"Bearer {issuer('eli', ['admins'])}"}
    assert client.put("/api/config", json={"values": {"cache_threshold": 0.96}}, headers=eli).status_code == 200
    assert client.post(f"/api/patterns/{p.id}/reject", headers=eli).json()["status"] == "rejected"
    # X-User-Id is ignored in cognito mode: identity comes from the JWT only
    me2 = client.get("/api/me", headers={**ben, "X-User-Id": "eli"}).json()
    assert me2["user_id"] == "ben"


@pytest.mark.acceptance("P6-02")
def test_local_persona_switcher(client):
    assert settings.auth == "local"
    seed.seed_users()
    for persona, groups in (("dana", []), ("chen", ["hr", "managers"]), ("eli", ["admins"])):
        me = client.get("/api/me", headers={"X-User-Id": persona}).json()
        assert me["user_id"] == persona and sorted(me["groups"]) == sorted(groups)
    assert client.get("/api/me", headers={"X-User-Id": "eli"}).json()["is_admin"]
    assert len(client.get("/api/users").json()) == 5                                       # the switcher's list


@pytest.mark.acceptance("P6-03")
def test_cors_only_from_the_web_origin(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "https://d111.cloudfront.net")
    import app.main as main

    prod = importlib.reload(main).app
    try:
        with TestClient(prod) as c:
            ok = c.options("/api/chat", headers={"Origin": "https://d111.cloudfront.net",
                                                 "Access-Control-Request-Method": "POST"})
            assert ok.headers.get("access-control-allow-origin") == "https://d111.cloudfront.net"
            bad = c.options("/api/chat", headers={"Origin": "https://evil.example.com",
                                                  "Access-Control-Request-Method": "POST"})
            assert "access-control-allow-origin" not in bad.headers
    finally:
        monkeypatch.setattr(settings, "cors_origins", "*")
        importlib.reload(main)


@pytest.mark.acceptance("P6-03")
def test_static_export_targets_a_configurable_api_origin():
    """The export reads NEXT_PUBLIC_API_BASE at build time; nothing hard-codes an API host."""
    src = (ROOT / "web" / "lib" / "api.ts").read_text()
    assert "process.env.NEXT_PUBLIC_API_BASE" in src
    assert 'output: "export"' in (ROOT / "web" / "next.config.js").read_text()
    for f in (ROOT / "web").rglob("*.tsx"):
        if "node_modules" in f.parts or ".next" in f.parts:
            continue
        assert not re.search(r"https?://(localhost|127\.0\.0\.1):8[01]8\d", f.read_text()), f


@pytest.mark.acceptance("P6-04")
def test_retention_deletes_expired_data_and_nothing_else(fresh_db, monkeypatch):
    install("conversion")
    t0 = time.time()
    with time_machine.travel(t0, tick=False):
        old = engine.route("Write a haiku about rivers", "ava")
        engine.route("Convert 3 kg to lb", "ava")
    with db.session() as s:
        counts = {m.__name__: s.scalar(select(func.count()).select_from(m))
                  for m in (RequestLog, Turn, CacheEntry, LedgerRow, OverheadRow, PatternRow, Conversation)}
    assert counts["RequestLog"] == 2 and counts["Turn"] == 4 and counts["CacheEntry"] >= 1
    later = t0 + settings.log_retention_days * 86400 + 3600
    with time_machine.travel(later, tick=False):
        fresh = engine.route("Write a poem about dawn", "ben")              # within its TTL
        out = retention.run()
    assert out["request_logs"] == 2 and out["turns"] == 4 and out["cache_entries"] >= 1
    with db.session() as s:
        after = {m.__name__: s.scalar(select(func.count()).select_from(m))
                 for m in (RequestLog, Turn, CacheEntry, LedgerRow, OverheadRow, PatternRow)}
        assert s.get(RequestLog, fresh.request_id) is not None and s.get(RequestLog, old.request_id) is None
    assert after["LedgerRow"] == counts["LedgerRow"] + 1                       # the ledger is never pruned
    assert after["OverheadRow"] == counts["OverheadRow"] and after["PatternRow"] == counts["PatternRow"]
    assert after["RequestLog"] == 1 and after["Turn"] == 2


@pytest.mark.acceptance("P6-05")
def test_terraform_validates_with_https_cognito_cloudfront_and_site():
    tf = ROOT / "infra" / "terraform"
    text = "\n".join(p.read_text() for p in tf.glob("*.tf"))
    for res in ('resource "aws_lb_listener" "https"', 'protocol          = "HTTPS"', 'type = "redirect"',
                'resource "aws_acm_certificate"', 'resource "aws_cognito_user_pool"', 'resource "aws_cloudfront_distribution"',
                'resource "aws_s3_bucket" "web"', 'resource "aws_cloudfront_origin_access_control"',
                'resource "aws_s3_bucket_lifecycle_configuration"'):
        assert res in text, res
    binary = shutil.which("terraform") or str(Path.home() / ".local/bin/terraform")
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    subprocess.run([binary, "init", "-backend=false", "-input=false"], cwd=tf, check=True, capture_output=True, env=env)
    out = subprocess.run([binary, "validate", "-no-color"], cwd=tf, capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "Success" in out.stdout, out.stdout + out.stderr


@pytest.mark.acceptance("P6-05")
def test_ci_workflows():
    wf = ROOT / ".github" / "workflows"
    verify = (wf / "verify.yml").read_text()
    deploy = (wf / "deploy.yml").read_text()
    plan = (wf / "terraform-plan.yml").read_text()
    assert "make verify" in verify and "pull_request" in verify
    assert "uses: ./.github/workflows/verify.yml" in deploy and "amazon-ecr-login" in deploy
    assert "update-service" in deploy and "s3 sync out/" in deploy and "create-invalidation" in deploy
    assert "terraform plan" in plan and "pull_request" in plan
    if shutil.which("actionlint"):
        assert subprocess.run(["actionlint"], cwd=ROOT).returncode == 0


@pytest.mark.acceptance("P6-06")
def test_deploy_runbook_covers_every_manual_step():
    doc = (ROOT / "DEPLOY.md").read_text()
    for needle in ("Bedrock", "Model access", "model_prices", "terraform apply", "docker push", "s3 sync",
                   "create-invalidation", "cognito-idp", "Smoke checks", "make test-aws", "make demo-measure",
                   "aws-only", "P1-10", "egress", "certificate"):
        assert needle in doc, needle
    acceptance = ROOT / "reports" / "acceptance.md"
    if acceptance.exists():                                  # every aws-only item has its manual check listed
        for aid in re.findall(r"\| (P\d-\d\d) \|[^\n]*\*\*aws-only\*\*", acceptance.read_text()):
            assert aid in doc, aid


def test_review_admin_and_ownership_under_cognito(client, issuer, monkeypatch):
    ben = {"Authorization": f"Bearer {issuer()}"}
    eli = {"Authorization": f"Bearer {issuer('eli', ['admins'])}"}
    assert client.post("/api/patterns/discover", headers=ben).status_code == 403
    assert client.post("/api/drift/run", headers=ben).status_code == 403
    assert client.post("/api/patterns/discover", headers=eli).status_code == 200
    assert client.post("/api/drift/run", headers=eli).status_code == 200
    from app.models import Notification

    with db.session() as s:
        s.add(Notification(id="n1", user_id="ava", kind="run_waiting", title="t", created_at=time.time()))
    assert client.post("/api/notifications/n1/read", headers=ben).status_code == 404       # not Ben's
    ava = {"Authorization": f"Bearer {issuer('ava', ['support'])}"}
    assert client.post("/api/notifications/n1/read", headers=ava).status_code == 200
    from app.agent import runner
    from app.schemas import Pattern

    p = __import__("app.store", fromlist=["x"]).store().put_pattern(Pattern(name="p", task_type="classification"))
    run = runner.create_run(p.id, "ava", {})
    assert client.get(f"/api/agent/runs/{run['id']}", headers=ben).status_code == 403
    assert client.get(f"/api/agent/runs/{run['id']}/events", headers=ben).status_code == 403
    assert client.get(f"/api/agent/runs/{run['id']}", headers=ava).status_code == 200
    monkeypatch.setattr(settings, "cognito_audience", "")                                   # misconfigured
    assert client.get("/api/me", headers=ben).status_code == 401                           # fails closed


def test_review_retention_deletes_drift_samples(fresh_db):
    from app.models import DriftSample

    now = time.time()
    with db.session() as s:
        s.add(DriftSample(pattern_id="p", pattern_version=1, ts=now - 40 * 86400, prompt="raw prompt jane@example.com",
                          artifact="a", llm="b", agreement=0.0, source="live"))
        s.add(DriftSample(pattern_id="p", pattern_version=1, ts=now, prompt="recent", artifact="a", llm="a",
                          agreement=1.0, source="live"))
    out = retention.run(now)
    assert out["drift_samples"] == 1
    with db.session() as s:
        assert [d.prompt for d in s.scalars(select(DriftSample))] == ["recent"]
