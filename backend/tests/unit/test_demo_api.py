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
