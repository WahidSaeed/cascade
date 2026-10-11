"""Demo control endpoints (only when CASCADE_DEMO=true)."""
from app import seed
from app.config import settings


def test_demo_reset_restores_the_starting_state(client, monkeypatch):
    rows = seed.traffic()[:120]
    monkeypatch.setattr(seed, "traffic", lambda: rows)
    seed.build()
    assert client.get("/api/ledger/summary").json()["requests"] == 120
    client.post("/api/chat", json={"prompt": "Write a haiku about rivers"}, headers={"X-User-Id": "ava"})
    client.put("/api/config", json={"values": {"cache_threshold": 0.97}})
    assert settings.cache_threshold == 0.97
    out = client.post("/api/demo/reset").json()
    assert out["restored"] is True
    assert client.get("/api/ledger/summary").json()["requests"] == 120
    assert settings.cache_threshold == 0.95                    # Tools edits are undone too
    assert client.get("/api/demo/state").json()["seed"]["requests"] == 120
    # a conversion in a shape this (small) seed's pattern was built from, with a new number
    shape = next(x["prompt"] for x in rows if x["task"] == "conversion")
    import re

    r = client.post("/api/chat", json={"prompt": re.sub(r"\d+", "777", shape, count=1)}).json()
    assert r["route"] == "artifact", r["light"]                # artifacts survive the reset (and are warm)


def test_replay_traffic_routes_a_slice_of_the_week_in_the_background(client, monkeypatch):
    import time

    rows = [r for r in seed.traffic() if not r.get("collections")][:200]
    monkeypatch.setattr(seed, "traffic", lambda: rows)
    seed.seed_users()
    assert client.post("/api/demo/replay-traffic?n=12").json() == {"started": 12}
    assert client.post("/api/demo/replay-traffic?n=12").status_code == 409     # one at a time
    for _ in range(600):
        st = client.get("/api/demo/state").json()["replay"]
        if not st["running"]:
            break
        time.sleep(0.05)
    assert st == {"running": False, "done": 12, "total": 12}
    s = client.get("/api/ledger/summary").json()
    assert s["requests"] == 12 and s["simulated_requests"] == 12


def test_reset_stops_a_running_replay_first(client, monkeypatch):
    rows = seed.traffic()[:120]
    monkeypatch.setattr(seed, "traffic", lambda: rows)
    seed.build()
    monkeypatch.setattr(settings, "demo_latency_ms", 200)    # slow enough that the replay is still running
    client.post("/api/demo/replay-traffic?n=100")
    out = client.post("/api/demo/reset").json()
    assert out["restored"] is True
    st = client.get("/api/demo/state").json()
    assert st["replay"]["running"] is False
    assert client.get("/api/ledger/summary").json()["requests"] == 120     # nothing written after the restore


def test_building_the_seed_never_waits_for_the_on_stage_llm_latency(fresh_db, monkeypatch):
    import time

    rows = [r for r in seed.traffic() if r["task"] not in ("extraction", "conversion")][:30]   # LLM-route prompts
    monkeypatch.setattr(seed, "traffic", lambda: rows)
    monkeypatch.setattr(seed, "install_artifacts", lambda week_start, docs=None: {})
    monkeypatch.setattr(settings, "demo_latency_ms", 2500)
    t0 = time.time()
    seed.build()
    assert time.time() - t0 < 30 * 2.5                        # 30 LLM answers at 2.5 s each would be 75 s
    assert settings.demo_latency_ms == 2500                   # the live demo keeps its latency
