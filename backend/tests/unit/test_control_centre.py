"""Admin control centre: every artifact version is kept (history + code for diffs), an admin can roll back to any
earlier version (a new version, redeployed), usage is reported across users, and ml / dl artifacts retrain with
additional data or an attached integration."""
import json
from pathlib import Path

from app import engine, seed
from app import patterns as pat
from app.store import store
from tests.helpers import install
from tests.unit.test_phase6_acceptance import issuer  # noqa: F401 - the Cognito JWT fixture

ADMIN = {"X-User-Id": "eli"}
USER = {"X-User-Id": "ava"}
CONVERT = "Convert 5 km to miles"


def test_every_version_is_kept_with_its_code_and_what_produced_it(fresh_db, client):
    seed.seed_users()
    p = install("conversion")
    v1_code = p.handler_code
    p2 = pat.edit(p.id, pat.PatternEdit(handler_code=v1_code + "\n# reviewed\n"), by="eli")
    hist = client.get(f"/api/patterns/{p.id}/versions").json()
    assert [h["version"] for h in hist] == [2, 1] and hist[0]["current"] and not hist[1]["current"]
    assert hist[0]["change_note"] == "code edited by eli" and hist[0]["status"] == "draft"
    assert hist[1]["change_note"] == "rule written by the LLM"
    assert hist[1]["status"] == "approved" and hist[1]["approved_by"] == "eli" and hist[1]["verification_passed"]
    assert client.get(f"/api/patterns/{p.id}/versions/1").json()["handler_code"] == v1_code
    assert client.get(f"/api/patterns/{p.id}/versions/2").json()["handler_code"] == p2.handler_code
    assert client.get(f"/api/patterns/{p.id}/versions/9").status_code == 404
    assert client.get("/api/patterns/missing/versions").status_code == 404


def test_an_admin_rolls_back_to_an_earlier_version_and_it_serves_again(fresh_db, client):
    seed.seed_users()
    p = install("conversion")
    v1_code = p.handler_code
    # v2: a broken edit, verified and approved anyway would be wrong — it is only saved (draft, not serving)
    pat.edit(p.id, pat.PatternEdit(handler_code='def handle(prompt):\n    return "broken"\n'), by="eli")
    assert engine.route(CONVERT, "ava").route.value == "llm"            # nothing approved serves now
    r = client.post(f"/api/patterns/{p.id}/rollback", json={"version": 1}, headers=ADMIN).json()
    assert r == {"version": 3, "status": "approved", "change_note": "rollback to v1 by eli", "pending": None}
    cur = store().get_pattern(p.id)
    assert cur.handler_code == v1_code and cur.verification.passed and cur.approved_by == "eli"
    out = engine.route("Convert 50 km to miles", "ben")
    assert out.route.value == "artifact" and "31.07" in out.answer                  # v1's behaviour, for everyone
    hist = client.get(f"/api/patterns/{p.id}/versions").json()
    assert [h["version"] for h in hist] == [3, 2, 1]                     # history is never rewritten
    assert client.post(f"/api/patterns/{p.id}/rollback", json={"version": 3}, headers=ADMIN).status_code == 409
    assert client.post(f"/api/patterns/{p.id}/rollback", json={"version": 7}, headers=ADMIN).status_code == 404


def test_rollback_is_admin_only(fresh_db, client, issuer):  # noqa: F811
    seed.seed_users()
    p = install("conversion")
    pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# v2\n"), by="eli")
    ben = {"Authorization": f"Bearer {issuer()}"}
    admin = {"Authorization": f"Bearer {issuer(user='eli', groups=('admins',))}"}
    assert client.post(f"/api/patterns/{p.id}/rollback", json={"version": 1}, headers=ben).status_code == 403
    assert client.get(f"/api/patterns/{p.id}/versions", headers=ben).status_code == 200     # anyone may look
    assert client.post(f"/api/patterns/{p.id}/rollback", json={"version": 1}, headers=admin).json()["version"] == 3


def test_rolling_back_to_an_unverified_version_waits_for_verify_and_approve(fresh_db, client):
    seed.seed_users()
    p = install("conversion")
    pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# v2, never verified\n"), by="eli")
    pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# v3\n"), by="eli")
    r = client.post(f"/api/patterns/{p.id}/rollback", json={"version": 2}, headers=ADMIN).json()
    assert r["version"] == 4 and r["status"] == "draft" and "Verify first" in r["pending"]
    assert "# v2, never verified" in store().get_pattern(p.id).handler_code


def test_usage_shows_one_unified_artifact_serving_every_user(fresh_db, client):
    seed.seed_users()
    p = install("conversion")
    for i, u in enumerate(("ava", "ben", "ben", "chen")):
        engine.route(f"Convert {i + 2} km to miles", u)
    u = client.get(f"/api/patterns/{p.id}/usage", headers=USER).json()
    assert u["users_served_by_artifact"] == 3 and u["artifact_requests"] == 4
    ben = next(x for x in u["per_user"] if x["user_id"] == "ben")
    assert ben["artifact_requests"] == 2 and ben["name"] == "Ben" and ben["tokens_avoided"] > 0
    assert u["cost_avoided_usd"] > 0 and u["per_user"][0]["user_id"] == "ben"           # busiest first
    assert client.get("/api/patterns/missing/usage", headers=USER).status_code == 404


def test_retrain_adds_data_and_integrations_to_the_last_runs(fresh_db, client, monkeypatch):
    from app.agent import datasets, integrations, runner
    from app.config import settings

    seed.seed_users()
    monkeypatch.setattr(settings, "discovery_min", 25)
    p = _approved_ml(settings)
    csv = (Path(settings.demo_dir) / "data" / "labelled_tickets.csv").read_bytes()
    extra = datasets.register("eli", "more.csv", {"input": "ticket_text", "label": "label"}, "train", data=csv)
    integ = integrations.register("eli", "CRM tier", "http", "http://crm.internal/tier", "feature_source")
    integrations.decide(integ["id"], "eli", True)
    run = client.post(f"/api/patterns/{p.id}/train-source", headers=ADMIN, json={
        "code": p.source_code, "dataset_ids": [extra["id"]], "integration_ids": [integ["id"]]}).json()
    intake = runner.load(run["id"])["intake"]
    assert extra["id"] in intake["dataset_ids"] and len(intake["dataset_ids"]) == 2       # the old data + the new
    assert intake["integration_ids"] == [integ["id"]] and intake["instructions"].startswith("Retrain")
    assert runner.load(run["id"])["integration_spec"]["url"] == "http://crm.internal/tier"  # reaches the job
    # someone else's dataset cannot be added
    mine = datasets.register("ava", "a.csv", {"input": "ticket_text", "label": "label"}, "train", data=csv)
    other = client.post(f"/api/patterns/{p.id}/train-source", headers={"X-User-Id": "ben"},
                        json={"code": p.source_code, "dataset_ids": [mine["id"]]})
    assert other.status_code == 403


def _approved_ml(settings):
    """Ticket triage as an approved ml artifact (as in test_source_training's fixture)."""
    import random

    from app import demo_world as w
    from app.agent import datasets, runner
    from app.discovery import discover

    rng = random.Random(11)
    for i in range(260):
        engine.route(w.make_triage_prompt(rng, w.TRIAGE_CLASSES[i % 6])[0], "ben")
    p = next(x for x in discover() if x.name == "Ticket triage")
    csv = (Path(settings.demo_dir) / "data" / "labelled_tickets.csv").read_bytes()
    ds = datasets.register("ava", "t.csv", {"input": "ticket_text", "label": "label"}, "both", data=csv)
    run = runner.create_run(p.id, "ava", {"dataset_ids": [ds["id"]], "instructions": "go straight to ml"})
    run = runner.start_run(run["id"], sync=True)
    runner.respond(run["id"], "accept", "", "ava", sync=True)
    pat.approve(p.id, "eli")
    return store().get_pattern(p.id)


def test_existing_artifacts_get_their_current_version_recorded_by_the_migration(tmp_path):
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from alembic import command

    here = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0006_scale")
    eng = create_engine(url)
    data = {"id": "pat_x", "name": "x", "kind": "rule", "version": 4, "handler_code": "def handle(p): ...",
            "centroid": [0.1] * 4, "status": "approved"}
    with eng.begin() as c:
        c.execute(text("INSERT INTO patterns (id, name, status, task_type, kind, data, created_at, updated_at) "
                       "VALUES ('pat_x', 'x', 'approved', 'transform', 'rule', :d, 0, 0)"), {"d": json.dumps(data)})
        c.execute(text("INSERT INTO patterns (id, name, status, task_type, kind, data, created_at, updated_at) "
                       "VALUES ('pat_y', 'y', 'recognized', NULL, NULL, '{}', 0, 0)"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        rows = c.execute(text("SELECT pattern_id, version, status, data FROM pattern_versions")).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [("pat_x", 4, "approved")]          # no artifact, no version
    snap = json.loads(rows[0][3]) if isinstance(rows[0][3], str) else rows[0][3]
    assert snap["handler_code"] == "def handle(p): ..." and "centroid" not in snap
    eng.dispose()
