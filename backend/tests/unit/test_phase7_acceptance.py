"""Phase 7 acceptance (BUILD.md §13): ledger rollups reconcile with the raw ledger and feed the dashboard;
artifact Lambdas live in private subnets with no NAT; the in-memory index stays the quick-start path.
pgvector equivalence (P7-01) runs against Postgres in tests/integration/test_phase7_pg.py; the latency and
load targets (P7-04) in tests/perf; recall (P7-05) in tests/eval."""
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import db, engine, rollups
from app.config import settings
from app.ledger import LedgerEntry, OverheadEntry, ledger
from app.light.cache import LightCache, light_cache
from app.light.embedder import get_embedder
from app.light.normalize import normalize
from app.light.recognizer import CentroidRecognizer, recognizer
from app.models import LedgerRollup, LedgerRow, OverheadRollup
from app.schemas import Pattern, PatternStatus
from tests.helpers import install

ROOT = Path(__file__).resolve().parents[3]
HOUR = 3600
T0 = 1_760_000_400.0          # 10 minutes past an hour: rows straddle hour and day boundaries
CATEGORIES = ("compile", "verify", "label", "training", "drift", "ingestion", "reverify", "discovery", "agent")


def _cost(rng: random.Random, dyadic: bool) -> float:
    # dyadic costs (multiples of 2^-24) add exactly in any order, so totals can be compared with ==
    return rng.randint(0, 4000) / 2**24 if dyadic else rng.uniform(0, 0.02)


def _populate(seed: int, dyadic: bool = True, n: int = 400) -> None:
    rng = random.Random(seed)
    entries = []
    for i in range(n):
        route = rng.choice(["llm", "cache", "artifact"])
        method = None if route == "llm" else rng.choice(["cache_exact", "pattern_measured", "heuristic"])
        entries.append(LedgerEntry(
            request_id=f"r{seed}-{i}", user_id=rng.choice(["ava", "ben", "chloe", "dev"]), route=route,
            ts=T0 + rng.uniform(0, 3 * 86400), pattern_id=rng.choice(["p1", "p2"]) if route == "artifact" else None,
            input_tokens=rng.randint(0, 900), output_tokens=rng.randint(0, 400), embed_tokens=rng.randint(0, 30),
            prompt_tokens=rng.randint(0, 200), context_tokens=rng.randint(0, 600), history_tokens=rng.randint(0, 90),
            system_tokens=rng.randint(0, 50), cache_read_tokens=rng.randint(0, 40), cache_write_tokens=rng.randint(0, 40),
            tokens_avoided_in=rng.randint(0, 900), tokens_avoided_out=rng.randint(0, 400),
            context_avoided_tokens=rng.randint(0, 500), cost_actual_usd=_cost(rng, dyadic),
            cost_avoided_usd=_cost(rng, dyadic), runtime_cost_usd=_cost(rng, dyadic) / 64,
            embed_cost_usd=_cost(rng, dyadic) / 128, estimate_method=method, uncited=rng.random() < 0.1,
            simulated=rng.random() < 0.3, collection_ids=["colA"] if rng.random() < 0.3 else []))
    ledger().record_many(entries[: n // 2])
    for e in entries[n // 2:]:
        ledger().record(e)
    for i in range(60):
        ledger().record_overhead(OverheadEntry(
            category=rng.choice(CATEGORIES), input_tokens=rng.randint(0, 3000), output_tokens=rng.randint(0, 900),
            cost_usd=_cost(rng, dyadic), pattern_id=rng.choice(["p1", "p2", None]),
            user_id=rng.choice(["ava", "ben", None]), collection_id=rng.choice(["colA", None]),
            ts=T0 + rng.uniform(-HOUR, 3 * 86400)))


def _windows() -> list[tuple[float | None, float | None]]:
    h0 = rollups.bucket(T0)
    return [(None, None), (h0, h0 + 86400), (h0 + 5 * HOUR, h0 + 29 * HOUR), (h0 + 86400, None), (None, h0 + 50 * HOUR)]


def _all_views(raw: bool, frm: float | None, to: float | None) -> dict:
    L = ledger()
    return {
        "summary": L.summary(frm, to, raw=raw),
        "summary_user": L.summary(frm, to, user_id="ben", raw=raw),
        "summary_pattern": L.summary(frm, to, pattern_id="p1", raw=raw),
        "hour": L.timeseries("hour", frm, to, raw=raw),
        "day": L.timeseries("day", frm, to, raw=raw),
        "day_user": L.timeseries("day", frm, to, user_id="ava", raw=raw),
        "by_user": L.by_user(frm, to, raw=raw),
        "by_pattern": L.by_pattern(frm, to, raw=raw),
    }


@pytest.mark.acceptance("P7-02")
def test_rollups_give_exactly_the_raw_ledger_totals(fresh_db):
    _populate(1)
    for frm, to in _windows():
        assert _all_views(False, frm, to) == _all_views(True, frm, to), (frm, to)
    s = ledger().summary()
    assert s["requests"] == 400 and s["overhead_entries"] == 60


def _close(a, b, where) -> None:
    """Integers and strings equal; floats within 1e-9 (summation order is the only difference)."""
    assert type(a) is type(b) or {type(a), type(b)} <= {int, float}, (where, a, b)
    if isinstance(a, dict):
        assert a.keys() == b.keys(), where
        for k in a:
            _close(a[k], b[k], (*where, k) if isinstance(where, tuple) else (where, k))
    elif isinstance(a, list):
        assert len(a) == len(b), where
        for x, y in zip(a, b, strict=True):
            _close(x, y, where)
    elif isinstance(a, float):
        assert abs(a - b) <= 1e-9, (where, a, b)
    else:
        assert a == b, (where, a, b)


@pytest.mark.acceptance("P7-02")
def test_rollups_reconcile_with_real_valued_costs(fresh_db):
    """Arbitrary float costs: every count and token total is identical; dollar totals agree to the cent's
    millionth (only the summation order differs)."""
    _populate(2, dyadic=False)
    for frm, to in _windows():
        a, b = _all_views(False, frm, to), _all_views(True, frm, to)
        _close(a, b, (frm, to))


@pytest.mark.acceptance("P7-02")
def test_requests_routed_by_the_engine_land_in_the_rollups(fresh_db):
    install("extraction")
    for q in ["Extract the order number, email and amount: 'Order NP-12345 for jane@example.com was charged €40.90 twice'",
              "Write a product description for the Ridgeline 2 tent", "Write a product description for the Ridgeline 2 tent"]:
        engine.route(q, "ava")
    with db.session() as s:
        raw = s.scalar(select(func.count()).select_from(LedgerRow))
        rolled = s.scalar(select(func.sum(LedgerRollup.requests)))
    assert raw == rolled == 3
    assert ledger().summary() == ledger().summary(raw=True)


@pytest.mark.acceptance("P7-02")
def test_rebuild_reproduces_the_incremental_rollups(fresh_db):
    _populate(3)

    def snapshot():
        with db.session() as s:
            return (sorted(tuple(r) for r in s.execute(select(LedgerRollup.__table__)).all()),
                    sorted(tuple(r) for r in s.execute(select(OverheadRollup.__table__)).all()))

    before = snapshot()
    with db.session() as s:
        rollups.rebuild(s)
    assert snapshot() == before


@pytest.mark.acceptance("P7-02")
def test_the_dashboard_reads_the_rollups(fresh_db):
    """Delete the raw rows behind the rollups: the dashboard's (whole-hour) views still report them, while an
    unaligned range — which reads the raw ledger — sees nothing."""
    _populate(4, n=40)
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        before = c.get("/api/ledger/summary").json()
        with db.session() as s:
            s.execute(delete(LedgerRow))
        after = c.get("/api/ledger/summary").json()
        assert after["requests"] == before["requests"] == 40
        assert sum(b["requests"] for b in c.get("/api/ledger/timeseries?bucket=day").json()) == 40
        assert sum(u["requests"] for u in c.get("/api/ledger/by-user").json()) == 40
        assert c.get(f"/api/ledger/summary?frm={T0 - 1}").json()["requests"] == 0


# ----------------------------------------------------------------------------- P7-03
def _tf() -> str:
    return "\n".join(p.read_text() for p in (ROOT / "infra" / "terraform").glob("*.tf"))


def _block(text: str, header: str) -> str:
    start = text.index(header)
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i:j + 1]
    raise AssertionError(header)


@pytest.mark.acceptance("P7-03")
def test_terraform_artifact_lambdas_have_no_route_to_a_nat():
    tf = _tf()
    assert "aws_nat_gateway" not in tf                                   # no NAT anywhere in the VPC
    private_rt = _block(tf, 'resource "aws_route_table" "private"')
    assert "route {" not in private_rt and "aws_route\"" not in tf      # local route only
    assert not re.search(r'resource "aws_route" ', tf)
    endpoint = _block(tf, 'resource "aws_vpc_endpoint" "s3"')
    assert '"Gateway"' in endpoint and "aws_route_table.private.id" in endpoint
    subnets = _block(tf, 'resource "aws_subnet" "private"')
    assert "map_public_ip_on_launch" not in subnets
    ecs = (ROOT / "infra" / "terraform" / "ecs.tf").read_text()
    assert 'name = "CASCADE_ARTIFACT_SUBNET_IDS", value = join(",", aws_subnet.private[*].id)' in ecs
    assert 'name = "CASCADE_ARTIFACT_SG", value = aws_security_group.artifact.id' in ecs
    artifact_sg = _block(tf, 'resource "aws_security_group" "artifact"')
    assert "egress" not in artifact_sg                                   # egress only via the S3 prefix-list rule
    rule = _block(tf, 'resource "aws_vpc_security_group_egress_rule" "artifact_s3"')
    assert "aws_vpc_endpoint.s3.prefix_list_id" in rule and "cidr_ipv4" not in rule
    binary = shutil.which("terraform") or str(Path.home() / ".local/bin/terraform")
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    tfdir = ROOT / "infra" / "terraform"
    subprocess.run([binary, "init", "-backend=false", "-input=false"], cwd=tfdir, check=True, capture_output=True, env=env)
    out = subprocess.run([binary, "validate", "-no-color"], cwd=tfdir, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout + out.stderr


class _Lambda:
    class exceptions:
        ResourceNotFoundException = KeyError

    def __init__(self):
        self.created: dict = {}

    def get_function(self, **kw):
        raise KeyError

    def create_function(self, **kw):
        self.created = kw


@pytest.mark.acceptance("P7-03")
@pytest.mark.parametrize("kind, integrations, sg", [("rule", [], "sg-art"), ("ml", [], "sg-art"),
                                                    ("ml", ["int_x"], "sg-int")])
def test_every_artifact_lambda_deploys_into_the_private_subnets(fresh_db, monkeypatch, kind, integrations, sg):
    from app.deploy import deploy

    model = Path(settings.data_dir) / "m"
    model.mkdir(parents=True, exist_ok=True)
    (model / "model.onnx").write_bytes(b"x")
    p = Pattern(name="r", kind=kind, model_uri=str(model), integration_ids=integrations,
                handler_code="def handle(prompt):\n    return {'answer': prompt}\n" if kind == "rule" else "")
    for k, v in {"artifact_runtime": "lambda", "artifact_role_arn": "arn:aws:iam::123456789012:role/a",
                 "ml_runtime_image": "img", "artifact_subnet_ids": "subnet-a,subnet-b", "artifact_sg": "sg-art",
                 "artifact_integration_sg": "sg-int"}.items():
        monkeypatch.setattr(settings, k, v)
    client = _Lambda()
    deploy(p, client=client)
    assert client.created["VpcConfig"] == {"SubnetIds": ["subnet-a", "subnet-b"], "SecurityGroupIds": [sg]}


# ----------------------------------------------------------------------------- P7-01 (quick-start path)
@pytest.mark.acceptance("P7-01")
def test_sqlite_quick_start_keeps_the_in_memory_index(fresh_db):
    install("extraction")
    assert db.dialect_name() == "sqlite" and not LightCache.shared_index()
    engine.route("Write a product description for the Ridgeline 2 tent", "ava")
    again = engine.route("Please write a product description for the Ridgeline 2 tent", "ava")
    assert again.route.value == "cache" and again.light.stage_hit == "semantic_cache", again.light   # hnswlib
    assert any(len(idx) for idx in light_cache()._indexes.values())
    r = recognizer()
    assert isinstance(r, CentroidRecognizer) and not r._use_pg()


# ----------------------------------------------------------------------------- several API tasks, one database
@pytest.mark.acceptance("P7-01")
def test_another_tasks_approval_change_reaches_this_tasks_recognizer(fresh_db, monkeypatch):
    """Task B's recognizer notices task A's reject within recognizer_refresh_s; until it does, the engine still
    never serves the rejected artifact (status is read fresh on every artifact request)."""
    import time

    from app.light import recognizer as rec_mod
    from app.store import store

    p = install("conversion")
    q = "Convert 12 km to miles"
    assert engine.route(q, "ava").route.value == "artifact"
    stale = rec_mod.recognizer()
    row = store().get_pattern(p.id)                       # "task A" rejects without touching this process
    row.status = PatternStatus.rejected
    store().put_pattern(row)
    assert rec_mod.recognizer() is stale                  # polling disabled: this task has not noticed yet
    r = engine.route("Convert 13 km to miles", "ava")
    assert r.route.value == "llm"                         # …but the rejected artifact is not served
    monkeypatch.setattr(settings, "recognizer_refresh_s", 0.01)
    time.sleep(0.02)
    rec_mod.recognizer()                                  # notices the change, rebuilds in the background
    for _ in range(200):
        if rec_mod.recognizer() is not stale and not rec_mod._sync["rebuilding"]:
            break
        time.sleep(0.05)
    fresh = rec_mod.recognizer()
    q2 = "Convert 14 km to miles"
    assert fresh is not stale and fresh.recognize(normalize(q2), get_embedder().embed(q2)[0]).pattern_id is None


def test_pattern_hits_come_from_the_ledger_not_a_hot_row(fresh_db):
    from app.main import app

    p = install("conversion")
    for i in range(3):
        engine.route(f"Convert {i + 20} km to miles", "ava")
    with TestClient(app) as c:
        assert c.get(f"/api/patterns/{p.id}").json()["hits"] == 3
        assert next(x for x in c.get("/api/patterns").json() if x["id"] == p.id)["hits"] == 3


def test_parsed_pattern_cache_follows_updates(fresh_db):
    from app.store import store

    p = install("conversion")
    first = store().serving_pattern(p.id)
    assert store().serving_pattern(p.id) is first           # parsed once per version
    a = store().get_pattern(p.id)
    a.name = "Renamed elsewhere"
    store().put_pattern(a)                                 # e.g. by another API task
    assert store().serving_pattern(p.id).name == "Renamed elsewhere"


# ----------------------------------------------------------------------------- review fixes
class _FakeRedis:
    """Enough of the redis client for the hot cache (mget / set ex / delete / scan_iter)."""

    def __init__(self):
        self.data: dict[str, tuple[str, float]] = {}
        self.down = False

    def _check(self):
        if self.down:
            raise ConnectionError("cache unreachable")

    def mget(self, keys):
        self._check()
        import time as _t

        return [v for v, exp in (self.data.get(k, (None, 0.0)) for k in keys) for v in [v if exp > _t.time() else None]]

    def set(self, key, value, ex):
        self._check()
        import time as _t

        self.data[key] = (value, _t.time() + ex)

    def delete(self, *keys):
        self._check()
        for k in keys:
            self.data.pop(k, None)

    def scan_iter(self, match, count):
        self._check()
        return [k for k in list(self.data) if k.startswith(match.rstrip("*"))]


@pytest.fixture
def hot(fresh_db, monkeypatch):
    from app.light import hotcache

    fake = _FakeRedis()
    monkeypatch.setattr(settings, "exact_cache_url", "test://")
    hotcache.set_hot_cache(hotcache.HotExactCache(fake))
    yield fake
    settings.exact_cache_url = ""                          # before the reset hooks run in fresh_db's teardown
    hotcache.set_hot_cache(None)


def test_hot_exact_cache_fronts_the_database(hot):
    q = "Write a product description for the Ridgeline 2 tent"
    engine.route(q, "ava")
    assert not hot.data                                   # writes go to the database only
    assert engine.route(q, "ava").light.stage_hit == "exact_cache"
    assert len(hot.data) == 1                             # the database hit was copied to the hot cache
    (value, exp), = hot.data.values()
    import json as _json
    import time as _t

    entry = _json.loads(value)
    assert exp <= entry["expires_at"] + 1 and exp <= _t.time() + settings.exact_cache_ttl_s + 1
    from app.models import CacheEntry

    with db.session() as s:                               # gone from the database: only the hot copy remains
        s.execute(delete(CacheEntry))
    r = engine.route(q, "ava")
    assert r.route.value == "cache" and r.answer == entry["answer"]


def test_hot_cache_respects_scope_and_invalidation(hot):
    p = install("conversion")
    q = "Convert 12 km to miles"
    engine.route(q, "ava")                                # artifact answer, cached
    engine.route(q, "ava")                                # DB hit -> hot copy
    assert hot.data
    for value, _ in hot.data.values():                    # a user-scoped copy is never served to another user
        import json as _json

        e = _json.loads(value)
        assert e["scope"] == "global" or e["user_id"] == "ava"
    from app import patterns as pat

    pat.reject(p.id, )
    assert not hot.data                                    # rejecting the artifact dropped its hot copies


def test_hot_cache_fails_open(hot):
    q = "Write a product description for the Summit boots"
    engine.route(q, "ben")
    hot.down = True
    assert engine.route(q, "ben").light.stage_hit == "exact_cache"   # straight to the database


def test_hot_cache_is_flushed_on_reset(hot):
    q = "Write a product description for the camp stove"
    engine.route(q, "ben")
    engine.route(q, "ben")
    assert hot.data
    light_cache().reset()
    assert not hot.data


class _OrderedLambda(_Lambda):
    def __init__(self, exists=True):
        super().__init__()
        self.calls: list[str] = []
        self.exists = exists

    def get_function(self, **kw):
        if not self.exists:
            raise KeyError
        self.calls.append("get")

    def update_function_code(self, **kw):
        self.calls.append("code")

    def update_function_configuration(self, **kw):
        self.calls.append("config")

    def create_function(self, **kw):
        self.calls.append("create")
        self.created = kw

    def get_waiter(self, name):
        calls = self.calls

        class W:
            def wait(self, **kw):
                calls.append(f"wait:{name}")
        return W()


@pytest.mark.acceptance("P7-03")
@pytest.mark.parametrize("exists", [True, False])
def test_lambda_updates_wait_for_each_other(fresh_db, monkeypatch, exists):
    from app.deploy import deploy

    p = Pattern(name="r", kind="rule", handler_code="def handle(prompt):\n    return prompt\n")
    for k, v in {"artifact_runtime": "lambda", "artifact_role_arn": "arn:aws:iam::123456789012:role/a",
                 "artifact_subnet_ids": "subnet-a", "artifact_sg": "sg-art"}.items():
        monkeypatch.setattr(settings, k, v)
    c = _OrderedLambda(exists)
    deploy(p, client=c)
    if exists:
        assert c.calls == ["get", "config", "wait:function_updated_v2", "code", "wait:function_updated_v2"]
    else:
        assert c.calls == ["create", "wait:function_active_v2"]


@pytest.mark.acceptance("P7-03")
def test_vpc_config_fails_closed_without_the_artifact_group(fresh_db, monkeypatch):
    from app.deploy import DeployError, deploy

    p = Pattern(name="r", kind="rule", handler_code="def handle(prompt):\n    return prompt\n")
    for k, v in {"artifact_runtime": "lambda", "artifact_role_arn": "arn:aws:iam::123456789012:role/a",
                 "artifact_subnet_ids": "subnet-a", "artifact_sg": ""}.items():
        monkeypatch.setattr(settings, k, v)
    with pytest.raises(DeployError):
        deploy(p, client=_Lambda())


def test_terraform_lets_the_api_create_vpc_lambdas_and_reach_the_hot_cache():
    tf = _tf()
    task = _block(tf, 'data "aws_iam_policy_document" "task"')
    for action in ("ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "ec2:DescribeVpcs"):
        assert action in task
    cache = _block(tf, 'resource "aws_elasticache_serverless_cache" "exact"')
    assert "aws_subnet.private[*].id" in cache
    assert "CASCADE_EXACT_CACHE_URL" in (ROOT / "infra" / "terraform" / "ecs.tf").read_text()


def test_config_edits_reach_every_api_task(fresh_db, monkeypatch):
    """Task A saves an override; task B (this process) applies it within config_refresh_s, and resets keys that
    were removed (demo reset) back to their defaults."""
    import time as _t

    from app import tasksync
    from app.store import store

    default = settings.cache_threshold
    tasksync.mark_applied({})
    store().set_config("cache_threshold", 0.99)          # written by another task
    monkeypatch.setattr(settings, "config_refresh_s", 0.01)
    _t.sleep(0.02)
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        c.get("/api/health")
        assert settings.cache_threshold == 0.99
        with db.session() as s:
            from app.models import ConfigRow

            s.execute(delete(ConfigRow).where(ConfigRow.key == "cache_threshold"))
        _t.sleep(0.02)
        c.get("/api/health")
        assert settings.cache_threshold == default


def test_mutation_responses_carry_hits(fresh_db):
    from app.main import app

    p = install("conversion")
    engine.route("Convert 31 km to miles", "ava")
    with TestClient(app) as c:
        r = c.put(f"/api/patterns/{p.id}", json={"name": "Unit conversion (renamed)"}, headers={"X-User-Id": "eli"})
        assert r.status_code == 200 and r.json()["hits"] == 1


def test_an_older_rebuild_never_replaces_a_newer_recognizer(fresh_db):
    from app.light import recognizer as rec_mod

    newer, older = CentroidRecognizer(shared=False), CentroidRecognizer(shared=False)
    rec_mod.set_recognizer(newer, (200.0, "b"))
    rec_mod.set_recognizer(older, (100.0, "a"))
    assert rec_mod._recognizer is newer


def test_config_sync_never_fails_a_request(fresh_db, monkeypatch):
    """A broken connection (e.g. the database was just restored from a snapshot) skips the sync; the request
    itself still succeeds and the next one retries."""
    from app import tasksync
    from app.store import SqlStore

    def boom(self):
        raise RuntimeError("terminating connection due to administrator command")

    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:   # started normally
        monkeypatch.setattr(SqlStore, "config_overrides", boom)
        monkeypatch.setattr(settings, "config_refresh_s", 0.01)
        tasksync._state["checked"] = 0.0
        assert c.get("/api/health").status_code == 200
    assert tasksync._state["checked"] == 0.0               # retried on the next request
