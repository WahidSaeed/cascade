"""Phase 7 performance targets (BUILD.md §13, P7-04), measured by `make perf` on the build machine without
coverage instrumentation:
- light layer p95 < 25 ms over 1,000 prompts, and < 60 ms for knowledge-scoped prompts (retrieval included);
- artifact-route p95 < 150 ms end to end (the artifact runs out of process in its persistent worker);
- 60 seconds at 50 requests/s against a running API (uvicorn + Locust) with no errors.
"""
import csv
import os
import random
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from app import db, engine, seed
from app import demo_world as w
from app.config import settings
from tests.helpers import install

LIGHT = ("normalize", "exact_cache", "embed", "semantic_cache", "recognizer")
KNOWLEDGE = LIGHT + ("retrieve",)
BACKEND = Path(__file__).resolve().parents[2]
API_WORKERS = 3                     # API tasks behind the load balancer
LOAD_P95_MS = 1500.0


@pytest.fixture(params=["sqlite", "postgres"])
def store_db(request, fresh_db, monkeypatch):
    """Each latency target holds on both stores: SQLite + in-memory hnswlib (quick start) and Postgres + pgvector
    (the Phase 7 deployment path: exact lookups, semantic cache and pattern kNN all query the database)."""
    if request.param == "postgres":
        from tests.conftest import _pg_fresh_database

        base = os.environ.get("CASCADE_TEST_DATABASE_URL")
        assert base, "make perf provides CASCADE_TEST_DATABASE_URL (docker-compose Postgres + pgvector)"
        monkeypatch.setattr(settings, "database_url", _pg_fresh_database(base))
        db.reset_engine()
        from app import state

        state.reset_runtime_state()
    yield request.param


def p95(xs: list[float]) -> float:
    return statistics.quantiles(xs, n=20)[18]


def stage_ms(resp, stages) -> float:
    st = resp.light.latency_ms_by_stage
    return sum(st.get(k, 0.0) for k in stages)


def _mixed_prompts(n: int, rng: random.Random) -> list[str]:
    """Realistic mix: open-ended generation (LLM), conversions + extractions (artifacts), and repeats /
    rewordings that the caches answer."""
    out: list[str] = []
    while len(out) < n:
        r = rng.random()
        if r < 0.35:
            out.append(w.make_open_ended(rng) + f" (draft {len(out)})")
        elif r < 0.55:
            out.append(w.make_conversion(rng))
        elif r < 0.75:
            out.append(w.make_extraction(rng))
        elif out:
            prev = rng.choice(out)
            out.append(prev if rng.random() < 0.5 else "Please " + prev[0].lower() + prev[1:])
    return out


@pytest.mark.acceptance("P7-04")
def test_light_layer_p95_over_1000_prompts(store_db):
    install("extraction")
    install("conversion")
    rng = random.Random(7)
    for q in _mixed_prompts(50, random.Random(1)):            # warm-up (model, workers, caches)
        engine.route(q, "warm")
    samples = [stage_ms(engine.route(q, rng.choice(["ava", "ben", "chloe"])), LIGHT) for q in _mixed_prompts(1000, rng)]
    v = p95(samples)
    print(f"[{store_db}] light layer over {len(samples)} prompts: p50 {statistics.median(samples):.1f} ms, p95 {v:.1f} ms")
    assert len(samples) == 1000 and v < 25.0, f"light p95 {v:.1f} ms"


@pytest.mark.acceptance("P7-04")
def test_knowledge_scoped_light_layer_p95_over_1000_prompts(store_db):
    seed.seed_users()
    seed.seed_knowledge()
    collection_ids = [seed.collection_id(k) for k in ("handbook", "products", "playbook", "compensation")]
    rng = random.Random(11)
    questions = [q for q, _, _ in w.KNOWLEDGE_QUESTIONS]
    users = [p["id"] for p in w.PERSONAS]

    def prompt(i: int) -> str:
        q = rng.choice(questions)
        return q if rng.random() < 0.4 else f"{q} (asked by team {i % 37})"

    for i in range(30):
        engine.route(prompt(i), users[i % len(users)], collection_ids=collection_ids)
    samples = [stage_ms(engine.route(prompt(i), rng.choice(users), collection_ids=collection_ids), KNOWLEDGE)
               for i in range(1000)]
    v = p95(samples)
    print(f"[{store_db}] knowledge-scoped light layer over {len(samples)} prompts: p50 {statistics.median(samples):.1f} ms, "
          f"p95 {v:.1f} ms")
    assert v < 60.0, f"knowledge-scoped light p95 {v:.1f} ms"


@pytest.mark.acceptance("P7-04")
def test_artifact_route_p95_under_150ms(store_db):
    install("extraction")
    install("conversion")
    rng = random.Random(5)
    makers = [w.make_extraction, w.make_conversion]
    for _ in range(20):
        engine.route(rng.choice(makers)(rng), "warm")
    lat = []
    for i in range(400):
        r = engine.route(rng.choice(makers)(rng), f"u{i % 9}")
        if r.route.value == "artifact":
            lat.append(r.latency_ms)
    v = p95(lat)
    print(f"[{store_db}] artifact route over {len(lat)} requests: p50 {statistics.median(lat):.1f} ms, p95 {v:.1f} ms")
    assert len(lat) >= 300 and v < 150.0, f"artifact p95 {v:.1f} ms"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.acceptance("P7-04")
def test_load_60s_at_50_rps_without_errors(fresh_db, tmp_path, monkeypatch):
    """The Phase 7 deployment shape: several API processes sharing one Postgres + pgvector database (the shared
    semantic cache and pattern index are what make more than one API task possible)."""
    from tests.conftest import _pg_fresh_database

    base = os.environ.get("CASCADE_TEST_DATABASE_URL")
    assert base, "make perf provides CASCADE_TEST_DATABASE_URL (docker-compose Postgres + pgvector)"
    monkeypatch.setattr(settings, "database_url", _pg_fresh_database(base))
    db.reset_engine()
    install("extraction")
    install("conversion")
    db.reset_engine()                                      # hand the database to the API processes
    port = _free_port()
    env = {**os.environ, "CASCADE_DATABASE_URL": settings.resolved_database_url(), "CASCADE_DATA_DIR": settings.data_dir,
           "CASCADE_LLM": "demo", "CASCADE_DEMO_LATENCY_MS": "0", "CASCADE_AUTH": "local", "CASCADE_DEMO": "false",
           "CASCADE_DRIFT_SAMPLE_RATE": "0", "CASCADE_SCHEDULER": "false", "HF_HUB_OFFLINE": "1",
           # each API process gets its share of the machine, as each ECS task gets its vCPUs (ecs.tf)
           "CASCADE_EMBED_THREADS": str(max(1, (os.cpu_count() or 2) // API_WORKERS))}
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                            "--workers", str(API_WORKERS), "--log-level", "warning"], cwd=BACKEND, env=env)
    try:
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:  # noqa: S310
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("API did not start")
        # warm every API process (model session, artifact workers, connection pool) before measuring: new
        # connections are spread across the uvicorn workers
        import json as _json

        wrng = random.Random(3)
        for q in _mixed_prompts(90, wrng):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=_json.dumps({"prompt": q}).encode(),
                                         headers={"Content-Type": "application/json", "X-User-Id": "warm"})
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                assert r.status == 200
        prefix = tmp_path / "load"
        out = subprocess.run([sys.executable, "-m", "locust", "-f", str(BACKEND / "tests" / "perf" / "locustfile.py"),
                              "--headless", "-u", "50", "-r", "50", "-t", "60s", "--host", f"http://127.0.0.1:{port}",
                              "--csv", str(prefix), "--only-summary", "--stop-timeout", "10"],
                             cwd=BACKEND, env=env, capture_output=True, text=True, timeout=240)
        rows = {r["Name"]: r for r in csv.DictReader(open(f"{prefix}_stats.csv"))}
        total = rows["Aggregated"]
        n, failures = int(total["Request Count"]), int(total["Failure Count"])
        rps = float(total["Requests/s"])
        print(f"load: {n} requests in 60 s ({rps:.1f} rps), {failures} failures, "
              f"p50 {total['50%']} ms, p95 {total['95%']} ms, p99 {total['99%']} ms")
        for name, r in rows.items():
            if name != "Aggregated":
                print(f"  {name}: {r['Request Count']} requests, p95 {r['95%']} ms")
        assert failures == 0, out.stdout[-3000:] + out.stderr[-3000:]
        # 50 users x 1 request/s offered; Locust's rate includes the 1 s ramp, so "keeps up" = >= 95 % of it
        assert rps >= 47.5, f"sustained only {rps:.1f} rps ({n} requests)"
        # stable latency at the target rate (§13): the Demo LLM answers instantly here, so this bounds Cascade's
        # own queueing — three API processes on a shared dev machine
        p95_ms = float(total["95%"])
        assert p95_ms <= LOAD_P95_MS, f"p95 {p95_ms:.0f} ms at {rps:.1f} rps"
    finally:
        api.terminate()
        try:
            api.wait(timeout=20)
        except subprocess.TimeoutExpired:
            api.kill()
