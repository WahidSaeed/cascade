from fastapi.testclient import TestClient

from app import crm_mock, seed
from app.store import store


def test_seed_users(fresh_db):
    seed.seed_users()
    personas = store().list_users(personas_only=True)
    assert {p["id"] for p in personas} == {"ava", "ben", "chen", "dana", "eli"}
    assert store().get_user("eli")["is_admin"] is True


def test_users_endpoint(client):
    seed.seed_users()
    assert len(client.get("/api/users").json()) == 5


def test_crm_mock():
    c = TestClient(crm_mock.app)
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/customers/tier", params={"email": "Jane@example.com"}).json()["tier"] == "gold"
    assert c.get("/customers/tier", params={"email": "x@example.com"}).json()["tier"] == "standard"


def test_seed_build_snapshot_and_reset(fresh_db, monkeypatch):
    rows = seed.traffic()[:120]
    monkeypatch.setattr(seed, "traffic", lambda: rows)
    info = seed.build()
    assert info["requests"] == 120 and set(info["patterns"]) == {"extraction", "conversion", "urgency", "vacation"}
    from app.ledger import ledger

    before = ledger().summary()
    assert before["requests"] == 120 and before["simulated_requests"] == 120 and before["overhead_entries"] > 0
    from app import engine

    engine.route("Write a haiku about rivers", "ava")              # live traffic after the demo starts
    assert ledger().summary()["requests"] == 121
    out = seed.reset()                                             # back to the starting state
    assert out["restored"] is True and out["seconds"] < 30
    assert ledger().summary()["requests"] == 120
    assert len(store().approved_patterns()) == 4
