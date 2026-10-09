from fastapi.testclient import TestClient

from app import crm_mock, seed
from app.store import store


def test_seed_users(fresh_db):
    seed.reset()
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
