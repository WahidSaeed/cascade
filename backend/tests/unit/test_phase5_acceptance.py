"""Phase 5 acceptance (BUILD.md §11): the model-building agent."""
import json
import random
import socket
import threading
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import func, select

from app import db, engine, seed
from app import demo_world as w
from app import patterns as pat
from app.agent import datasets, integrations, runner, tools
from app.config import settings
from app.discovery import discover, notifications
from app.ledger import ledger
from app.models import OverheadRow
from app.schemas import PatternStatus
from app.store import store

CSV = (Path(settings.demo_dir) / "data" / "labelled_tickets.csv").read_bytes()


def _traffic(n: int = 320, seed_: int = 11) -> None:
    rng = random.Random(seed_)
    users = [u["id"] for u in w.ALL_USERS]
    for i in range(n):
        cls = w.TRIAGE_CLASSES[i % len(w.TRIAGE_CLASSES)]
        engine.route(w.make_triage_prompt(rng, cls)[0], rng.choice(users))
    for i in range(40):
        engine.route(w.make_open_ended(rng) + f" ({i})", rng.choice(users))


@pytest.fixture
def triage(fresh_db, monkeypatch):
    seed.seed_users()
    monkeypatch.setattr(settings, "discovery_min", 25)
    _traffic()
    found = discover()
    return next(p for p in found if p.name == "Ticket triage")


def _overhead(run_id=None, category=None):
    with db.session() as s:
        q = select(func.count(), func.coalesce(func.sum(OverheadRow.cost_usd), 0.0))
        if run_id:
            q = q.where(OverheadRow.run_id == run_id)
        if category:
            q = q.where(OverheadRow.category == category)
        return s.execute(q).one()


@pytest.mark.acceptance("P5-01")
def test_discovery_notifies_and_intake_waits_for_start(triage, client):
    note = next(n for n in notifications("ava") if n["kind"] == "pattern_recognized")
    assert "Ticket triage" in note["title"] and note["link"].endswith(f"?id={triage.id}")
    intake = client.get(f"/api/patterns/{triage.id}/intake").json()
    assert intake["examples"] and intake["frequency_per_day"] > 0 and intake["projected_tokens_per_day"] > 0
    assert "rules first" in intake["suggested_plan"] and intake["budget"]["max_cost_usd"] > 0
    assert intake["first_iteration_estimate_usd"] > 0 and intake["pattern"]["task_type"] == "classification"
    run = client.post("/api/agent/runs", json={"pattern_id": triage.id, "instructions": "six classes"},
                      headers={"X-User-Id": "ava"}).json()
    assert run["status"] == "awaiting_intake" and run["intake"]["instructions"] == "six classes"
    time.sleep(0.3)
    run = client.get(f"/api/agent/runs/{run['id']}", headers={"X-User-Id": "ava"}).json()
    assert run["status"] == "awaiting_intake" and run["candidates"] == {}            # nothing runs before Start
    assert _overhead(run["id"])[0] == 0
    assert store().get_pattern(triage.id).status == PatternStatus.awaiting_intake


@pytest.mark.acceptance("P5-02")
def test_uploaded_labelled_csv_is_validated_scanned_and_preferred(triage):
    ds = datasets.register("ava", "labelled_tickets.csv", {"input": "ticket_text", "label": "label"}, "both", data=CSV)
    assert ds["rows"] == 300 and set(ds["report"]["label_distribution"]) == set(w.TRIAGE_CLASSES)
    assert ds["report"]["duplicates_of_logged_prompts"] >= 0
    # order numbers are identifiers: the PII scan redacts them before the data is used
    assert ds["pii_status"] == "redacted" and ds["report"]["pii_rows"] > 0
    assert not any("NP-" in r["input"] for r in datasets.load(ds["id"]))
    with pytest.raises(datasets.DatasetError):
        datasets.register("ava", "x.csv", {"input": "nope"}, "both", data=CSV)
    pii = b"ticket_text,label\nOrder NP-12345 for jane@example.com was charged twice,billing\n"
    red = datasets.register("ava", "p.csv", {"input": "ticket_text", "label": "label"}, "train", data=pii)
    assert red["pii_status"] == "redacted" and "jane@" not in datasets.load(red["id"])[0]["input"]
    with pytest.raises(datasets.DatasetError):
        datasets.register("ava", "p.csv", {"input": "ticket_text", "label": "label"}, "train", data=pii,
                          pii_action="reject")
    # user labels take precedence: a logged prompt the user labelled differently is trained/evaluated with the user's label
    logged = tools.Toolbox("x", triage, {"intake": {}, "budget": {"max_cost_usd": 1}}, "ava")._logged()
    text = w.quoted(logged[0][0])
    flipped = "account" if logged[0][1] != "account" else "billing"
    override = datasets.register("ava", "o.csv", {"input": "ticket_text", "label": "label"}, "train",
                                 data=f"ticket_text,label\n\"{text}\",{flipped}\n".encode())
    run = runner.create_run(triage.id, "ava", {"dataset_ids": [ds["id"], override["id"]]})
    tb = tools.Toolbox(run["id"], triage, runner.load(run["id"]), "ava")
    tb.build_dataset()
    rows = tb.state["dataset"]["train"] + tb.state["dataset"]["holdout"] + tb.state["dataset"]["val"]
    match = [r for r in rows if w.quoted(r["input"]) == text]
    assert match and all(r["label"] == flipped and r["source"] == "user" for r in match)
    assert len(tb.state["dataset"]["user_eval"]) == 75


@pytest.mark.acceptance("P5-03")
def test_fuzzy_classification_rule_to_ml_feedback_accept_deploy(triage, monkeypatch):
    ds = datasets.register("ava", "labelled_tickets.csv", {"input": "ticket_text", "label": "label"}, "both", data=CSV)
    run = runner.create_run(triage.id, "ava", {"instructions": "Use these six classes; refund is separate from returns",
                                                "dataset_ids": [ds["id"]]})
    run = runner.start_run(run["id"], sync=True)
    it1 = run["iterations"][-1]
    assert run["status"] == "awaiting_feedback" and it1["candidate"]["kind"] == "rule"
    assert it1["metrics"]["agreement"] < 0.8 and "ML" in it1["report_md"]
    run = runner.respond(run["id"], "improve", "", "ava", sync=True)
    it2 = run["iterations"][-1]
    assert it2["candidate"]["kind"] == "ml" and it2["metrics"]["agreement"] >= 0.9
    run = runner.respond(run["id"], "improve", "treat 'refund' as its own class: refund means money back", "ava",
                         sync=True)
    it3 = run["iterations"][-1]
    assert it3["candidate"]["params"]["ngram_max"] == 2 and it3["candidate"]["params"]["class_weight"] == "balanced"
    assert it3["metrics"]["agreement"] >= it2["metrics"]["agreement"] and it3["metrics"]["agreement"] >= 0.9
    run = runner.respond(run["id"], "accept", "", "ava")
    assert run["status"] == "accepted"
    p = store().get_pattern(triage.id)
    assert p.status == PatternStatus.draft and p.kind.value == "ml" and p.verification.passed   # not self-approved
    # an admin approves: deployed as a container Lambda (moto) ...
    with mock_aws():
        iam = boto3.client("iam", region_name="us-east-1")
        role = iam.create_role(RoleName="a", AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))["Role"]["Arn"]
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cascade-art")
        monkeypatch.setattr(settings, "artifact_runtime", "lambda")
        monkeypatch.setattr(settings, "artifact_role_arn", role)
        monkeypatch.setattr(settings, "artifact_bucket", "cascade-art")
        monkeypatch.setattr(settings, "ml_runtime_image", "123456789012.dkr.ecr.us-east-1.amazonaws.com/ml-runtime:1")
        approved = pat.approve(triage.id, "eli")
        fn = boto3.client("lambda", region_name="us-east-1").get_function(FunctionName=approved.deployed_function)
        assert fn["Configuration"]["PackageType"] == "Image"
        keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="cascade-art")["Contents"]]
        assert any(k.endswith("model.onnx") for k in keys)
    # ... and (locally) serves traffic with confidence-based fallback
    monkeypatch.setattr(settings, "artifact_runtime", "subprocess")
    from app.runtime import set_runtime

    set_runtime("subprocess", None)
    hit = engine.route("Classify this support ticket: 'The zip on my backpack broke after two uses'", "ben")
    assert hit.route.value == "artifact" and hit.answer == "technical" and hit.artifact_kind == "ml"
    vague = engine.route("Classify this support ticket: 'hmm ok'", "ben")
    assert vague.route.value == "llm"                                             # low confidence -> LLM


@pytest.mark.acceptance("P5-04")
def test_dl_run_through_the_training_job_interface(fresh_db, monkeypatch):
    seed.seed_users()
    from app.schemas import Pattern, TaskType

    rng = random.Random(3)
    p = Pattern(name="Urgency scoring", task_type=TaskType.scoring, status=PatternStatus.recognized)
    pat.shape_from_examples(p, [w.make_urgency(rng) for _ in range(40)])
    store().put_pattern(p)
    data = (Path(settings.demo_dir) / "data" / "urgency_train.jsonl").read_bytes()
    ds = datasets.register("eli", "u.jsonl", {"input": "prompt", "label": "label"}, "both", data=data)
    starts = []
    real_start = tools.dlc.LocalTrainer.start

    def spy(self, data_path, out_dir, params):
        starts.append(params)
        return real_start(self, data_path, out_dir, params)

    monkeypatch.setattr(tools.dlc.LocalTrainer, "start", spy)
    run = runner.create_run(p.id, "eli", {"dataset_ids": [ds["id"]]})
    run = runner.start_run(run["id"], sync=True)
    it = run["iterations"][-1]
    assert starts, [e["text"] for e in run["events"]][-5:]
    assert it["candidate"]["kind"] == "dl" and it["metrics"]["agreement"] >= 0.9
    assert _overhead(run["id"], "training")[1] > 0
    run = runner.respond(run["id"], "accept", "", "eli")
    assert run["status"] == "accepted"
    pat.approve(p.id, "eli")
    r = engine.route("Rate the urgency 1-5: 'My trek starts tomorrow and my boots haven't arrived'", "ava")
    assert r.route.value == "artifact" and r.artifact_kind == "dl" and r.answer in ("4", "5")
    assert engine.route("Rate the urgency 1-5: 'hmm'", "ava").route.value == "llm"   # input guard -> LLM


def test_sagemaker_trainer_job_api():
    from botocore.stub import ANY, Stubber

    from app.compilers.dl import DlJob, SageMakerTrainer

    sm = boto3.client("sagemaker", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    stub, s3stub = Stubber(sm), Stubber(s3)
    s3stub.add_response("put_object", {}, {"Bucket": ANY, "Key": ANY, "Body": ANY})
    stub.add_response("create_training_job", {"TrainingJobArn": "arn:aws:sagemaker:us-east-1:1:training-job/x"}, None)
    stub.add_response("describe_training_job", {"TrainingJobName": "x", "TrainingJobArn": "arn", "TrainingJobStatus":
                      "Completed", "SecondaryStatus": "Completed", "AlgorithmSpecification": {"TrainingInputMode": "File"},
                      "ResourceConfig": {"InstanceCount": 1, "VolumeSizeInGB": 10}, "StoppingCondition": {},
                      "CreationTime": "2026-01-01", "ModelArtifacts": {"S3ModelArtifacts": "s3://b/m"}}, {"TrainingJobName": ANY})
    with stub, s3stub:
        t = SageMakerTrainer(sm, s3)
        settings.artifact_bucket = "b"
        settings.sagemaker_role_arn = "arn:aws:iam::123456789012:role/sm"
        job = t.start(Path(__file__), Path("/tmp/x"), {"epochs": 1})
        assert t.status(DlJob(job.job_id, Path("/tmp/x"), 0, "sagemaker")) == "Completed"
    settings.artifact_bucket = ""


def _crm_server():
    import uvicorn

    from app.crm_mock import app as crm

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(crm, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


@pytest.mark.integration          # starts crm-mock on localhost: runs in the integration suite (unit tests have no sockets)
@pytest.mark.acceptance("P5-05")
def test_integration_needs_admin_approval_and_flags_non_determinism(fresh_db, client, monkeypatch):
    seed.seed_users()
    monkeypatch.setattr(settings, "discovery_min", 20)
    rng = random.Random(8)
    for _ in range(80):
        engine.route(w.make_routing(rng), "ben")
    routing = next(p for p in discover() if p.name == "Priority routing")
    server, url = _crm_server()
    try:
        integ = client.post("/api/integrations", json={
            "name": "CRM tier lookup", "type": "http", "target": f"{url}/customers/tier", "role": "feature_source",
            "config": {"method": "GET", "param": "email", "field": "tier", "map": {"gold": "priority-"}}},
            headers={"X-User-Id": "ben"}).json()
        assert integ["status"] == "pending"
        with pytest.raises(integrations.IntegrationError):
            integrations.call(integ["id"], {"email": "jane@example.com"})          # not approved yet
        ds = datasets.register("ben", "labelled_tickets.csv", {"input": "ticket_text", "label": "label"}, "train", data=CSV)
        run = client.post("/api/agent/runs", json={"pattern_id": routing.id, "integration_ids": [integ["id"]],
                                                   "dataset_ids": [ds["id"]], "instructions": "go straight to ml",
                                                   "start": True}, headers={"X-User-Id": "ben"})
        assert run.status_code == 409 and "approval" in run.json()["detail"]
        assert client.post(f"/api/integrations/{integ['id']}/approve", headers={"X-User-Id": "ben"}).status_code == 403
        assert client.post(f"/api/integrations/{integ['id']}/approve",
                           headers={"X-User-Id": "eli"}).json()["status"] == "approved"
        assert integrations.call(integ["id"], {"email": "jane@example.com"})["tier"] == "gold"
        r = runner.create_run(routing.id, "ben", {"integration_ids": [integ["id"]], "dataset_ids": [ds["id"]],
                                                   "instructions": "go straight to ml"})
        tb = tools.Toolbox(r["id"], routing, runner.load(r["id"]), "ben")
        with pytest.raises(tools.ToolError):
            tb.call_integration("int_unregistered", {})                          # only intake integrations
        r = runner.start_run(r["id"], sync=True)
        assert r["iterations"][-1]["candidate"]["kind"] == "ml" and r["iterations"][-1]["metrics"]["agreement"] >= 0.9
        r = runner.respond(r["id"], "accept", "", "ben")
        assert r["status"] == "accepted"
        p = pat.approve(routing.id, "eli")
        assert p.not_fully_deterministic
        out = engine.route("Which queue should handle this ticket from jane@example.com: 'I was charged twice for my "
                           "tent order'", "ava")
        assert out.route.value == "artifact" and out.answer == "priority-billing" and out.not_fully_deterministic
        std = engine.route("Which queue should handle this ticket from omar@example.com: 'my parcel never arrived'",
                           "ava")
        assert std.answer == "shipping"
    finally:
        server.should_exit = True


@pytest.mark.acceptance("P5-06")
def test_budget_cap_pauses_and_all_costs_are_overhead(triage):
    ds = datasets.register("ava", "labelled_tickets.csv", {"input": "ticket_text", "label": "label"}, "both", data=CSV)
    run = runner.create_run(triage.id, "ava", {"dataset_ids": [ds["id"]], "max_cost_usd": 0.004})
    run = runner.start_run(run["id"], sync=True)
    assert run["status"] == "awaiting_feedback" and "budget cap" in (run["cap_reached"] or "")
    spent = run["cost_so_far_usd"]
    n, usd = _overhead(run["id"])
    assert n > 0 and usd == pytest.approx(spent, rel=1e-6)                      # every agent cost is in the ledger
    run = runner.raise_budget(run["id"], max_cost_usd=1.0, sync=True)
    assert run["status"] == "awaiting_feedback" and run["iterations"]            # finished the iteration
    run = runner.respond(run["id"], "improve", "", "ava", sync=True)
    cats = {c for (c,) in db.session().__enter__().execute(select(OverheadRow.category).where(OverheadRow.run_id == run["id"]))}
    assert {"agent", "training"} <= cats
    # payback includes the agent's costs
    row = next(r for r in ledger().by_pattern() if r["pattern_id"] == triage.id)
    assert row["overhead_cost_usd"] >= _overhead(run["id"])[1] - 1e-8          # (8-decimal rounding)
    run = runner.respond(run["id"], "stop", "", "ava")
    assert run["status"] == "stopped" and store().get_pattern(triage.id).status == PatternStatus.recognized


@pytest.mark.acceptance("P5-07")
def test_killed_run_resumes_from_its_checkpoint(triage, monkeypatch):
    ds = datasets.register("ava", "labelled_tickets.csv", {"input": "ticket_text", "label": "label"}, "both", data=CSV)
    run = runner.create_run(triage.id, "ava", {"dataset_ids": [ds["id"]], "instructions": "go straight to ml"})
    calls = {"n": 0}
    real = tools.Toolbox.train_ml

    def crash(self, *a, **k):
        calls["n"] += 1
        raise SystemExit("worker killed")                                       # the ECS task dies mid-iteration

    monkeypatch.setattr(tools.Toolbox, "train_ml", crash)
    with pytest.raises(SystemExit):
        runner.start_run(run["id"], sync=True)
    saved = runner.load(run["id"])
    assert saved["status"] == "agent_running" and saved["step"] == 1           # build_dataset was checkpointed
    assert saved["dataset"]["train"]
    monkeypatch.setattr(tools.Toolbox, "train_ml", real)
    from app.models import AgentRunRow

    with db.session() as s:                                                      # the dead worker's lease expires
        s.get(AgentRunRow, run["id"]).lease_until = time.time() - 1
    from app.agent import worker

    assert worker.poll_once() == 1                                               # a new task picks it up
    done = runner.load(run["id"])
    assert done["status"] == "awaiting_feedback" and done["iterations"][-1]["candidate"]["kind"] == "ml"
    assert [e["tool"] for e in done["events"] if e["kind"] == "tool"].count("build_dataset") == 1   # not redone
