"""Regressions from the Phase 5 acceptance review."""
import io
import json
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

from app import patterns as pat
from app import seed
from app.agent import bedrock_agent, datasets, integrations, runner, tools
from app.config import settings
from app.schemas import Pattern, PatternStatus, TaskType
from app.store import store


@pytest.fixture
def pattern(fresh_db):
    seed.seed_users()
    p = Pattern(name="Ticket triage", task_type=TaskType.classification, status=PatternStatus.recognized,
                examples=["Classify this support ticket: 'my zip broke'"])
    return store().put_pattern(p)


def test_sagemaker_model_is_fetched_from_s3(tmp_path):
    from app.compilers.dl import DlJob, SageMakerTrainer

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("model.onnx", b"onnx"), ("meta.json", b"{}"), ("tokenizer.json", b"{}"), ("../evil", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    sm, s3 = boto3.client("sagemaker", region_name="us-east-1"), boto3.client("s3", region_name="us-east-1")
    st, s3t = Stubber(sm), Stubber(s3)
    st.add_response("describe_training_job", {"TrainingJobName": "j", "TrainingJobArn": "a", "TrainingJobStatus": "Completed",
                    "SecondaryStatus": "Completed", "AlgorithmSpecification": {"TrainingInputMode": "File"},
                    "ResourceConfig": {"InstanceCount": 1, "VolumeSizeInGB": 1}, "StoppingCondition": {},
                    "CreationTime": "2026-01-01", "ModelArtifacts": {"S3ModelArtifacts": "s3://b/models/j/model.tar.gz"}},
                    {"TrainingJobName": "j"})
    s3t.add_response("get_object", {"Body": io.BytesIO(buf.getvalue())}, {"Bucket": "b", "Key": "models/j/model.tar.gz"})
    with st, s3t:
        SageMakerTrainer(sm, s3).fetch(DlJob("j", tmp_path / "out", 0, "sagemaker"))
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["meta.json", "model.onnx", "tokenizer.json"]
    assert not (tmp_path / "evil").exists()                                  # no path traversal


def test_bedrock_tool_results_answer_every_tool_use(pattern):
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    stub = Stubber(client)
    sent = []
    for content in ([{"toolUse": {"toolUseId": "u1", "name": "train_ml", "input": {}}}],
                    [{"toolUse": {"toolUseId": "u2", "name": "report", "input": {"recommendation": "accept"}}}],
                    [{"text": "ok"}]):
        stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": content}},
                                       "stopReason": "tool_use", "usage": {"inputTokens": 10, "outputTokens": 5,
                                                                           "totalTokens": 15}, "metrics": {"latencyMs": 1}},
                          {"modelId": ANY, "messages": ANY, "toolConfig": ANY, "system": ANY, "inferenceConfig": ANY})
    run = runner.load(runner.create_run(pattern.id, "ava", {})["id"])
    tb = tools.Toolbox(run["id"], pattern, run, "ava")
    real_converse = client.converse

    def spy(**kw):
        sent.append(json.loads(json.dumps(kw["messages"], default=str)))
        return real_converse(**kw)

    client.converse = spy
    with stub:
        a = bedrock_agent.next_action(run, tb, client)
        run["pending_tool_use"] = a["tool_use_id"]
        run["events"].append({"n": 9, "kind": "error", "text": "Tool error: build_dataset first", "tool_use_id": "u1"})
        b = bedrock_agent.next_action(run, tb, client)
        run["pending_tool_use"] = b["tool_use_id"]
        run["feedback"].append("refund is its own class")
        bedrock_agent.next_action(run, tb, client)
    second, third = sent[1][-1]["content"][0]["toolResult"], sent[2][-1]["content"][0]["toolResult"]
    assert second["toolUseId"] == "u1" and second["status"] == "error"
    assert third["toolUseId"] == "u2" and "refund" in third["content"][0]["text"]


def test_lease_is_renewed_during_a_long_step(pattern, monkeypatch):
    monkeypatch.setattr(runner, "LEASE_S", 0.6)
    run = runner.create_run(pattern.id, "ava", {})
    runner.save(runner.load(run["id"]), "agent_running")
    started = threading.Event()

    def slow_step(r, owner):
        started.set()
        time.sleep(1.5)                                                      # longer than the lease
        return False

    monkeypatch.setattr(runner, "_step", slow_step)
    t = threading.Thread(target=runner.execute, args=(run["id"], "worker-a"))
    t.start()
    started.wait(2)
    time.sleep(0.9)
    assert not runner.acquire(run["id"], "worker-b")                          # still owned: heartbeat renewed it
    t.join()


def test_accept_errors_never_strand_a_run(pattern):
    run = runner.create_run(pattern.id, "ava", {})
    runner.save(runner.load(run["id"]), "awaiting_feedback")
    out = runner.respond(run["id"], "accept", "", "ava")                       # no candidate at all
    assert out["status"] == "awaiting_feedback" and "could not run" in out["events"][-1]["text"]


def test_spend_is_the_ledger_sum_and_extract_table_is_budgeted(fresh_db):
    seed.seed_users()
    docs = seed.seed_knowledge()
    p = store().put_pattern(Pattern(name="lookup", task_type=TaskType.lookup))
    run = runner.create_run(p.id, "chen", {"budget": None})
    tb = tools.Toolbox(run["id"], p, runner.load(run["id"]), "chen")
    tb.state["budget"]["max_cost_usd"] = 0.0
    with pytest.raises(tools.BudgetExceeded):
        tb.extract_table([docs["handbook_v1"]], "vacation days by country")
    tb.state["budget"]["max_cost_usd"] = 1.0
    tb.extract_table([docs["handbook_v1"]], "vacation days by country")
    from app.ledger import ledger

    with __import__("app.db", fromlist=["x"]).session() as s:
        from sqlalchemy import func, select

        from app.models import OverheadRow

        total = s.scalar(select(func.sum(OverheadRow.cost_usd)).where(OverheadRow.run_id == run["id"]))
    assert total > 0 and tb.spent() == pytest.approx(total) and tb.state["cost_so_far_usd"] == pytest.approx(total)
    assert ledger().summary()["overhead_cost_usd"] >= total


def test_runs_and_datasets_are_owned(pattern, client):
    run = client.post("/api/agent/runs", json={"pattern_id": pattern.id}, headers={"X-User-Id": "ava"}).json()
    for path, body in ((f"/api/agent/runs/{run['id']}/start", None),
                       (f"/api/agent/runs/{run['id']}/budget", {"max_cost_usd": 100}),
                       (f"/api/agent/runs/{run['id']}/respond", {"action": "stop"})):
        assert client.post(path, json=body, headers={"X-User-Id": "ben"}).status_code == 403
    assert client.post(f"/api/agent/runs/{run['id']}/budget", json={"max_cost_usd": 2},
                       headers={"X-User-Id": "eli"}).status_code == 200      # admins may
    ds = datasets.register("ava", "t.csv", {"input": "t", "label": "l"}, "train", data=b"t,l\na b c,x\n")
    assert client.get(f"/api/datasets/{ds['id']}", headers={"X-User-Id": "ben"}).status_code == 403
    assert client.post("/api/agent/runs", json={"pattern_id": pattern.id, "dataset_ids": [ds["id"]]},
                       headers={"X-User-Id": "ben"}).status_code == 403


def test_dedup_and_eval_rows_never_train(pattern):
    csv = b"t,l\nmy zip broke,technical\nmy zip broke,technical\nwhere is my parcel,shipping\n"
    ds = datasets.register("ava", "t.csv", {"input": "t", "label": "l"}, "train", data=csv)
    assert ds["rows"] == 2 and ds["report"]["duplicates_within_file_removed"] == 1
    ev = datasets.register("ava", "e.csv", {"input": "t", "label": "l"}, "eval", data=b"t,l\nmy zip broke,technical\n")
    run = runner.create_run(pattern.id, "ava", {"dataset_ids": [ds["id"], ev["id"]]})
    tb = tools.Toolbox(run["id"], pattern, runner.load(run["id"]), "ava")
    tb.build_dataset()
    train = [r["input"] for r in tb.state["dataset"]["train"]]
    assert not any("my zip broke" in t for t in train) and len(tb.state["dataset"]["user_eval"]) == 1


def test_rejecting_an_integration_demotes_its_artifacts(fresh_db):
    seed.seed_users()
    i = integrations.register("ben", "CRM", "http", "http://127.0.0.1:9/tier", "feature_source")
    p = Pattern(name="routing", task_type=TaskType.routing, kind="rule", status=PatternStatus.draft,
                handler_code="def handle(p):\n    return 'billing'\n", integration_ids=[i["id"]])
    p.verification = pat.VerifyResult(pattern_id=p.id, score=1.0, sample_size=10, passed=True)
    p.verification_score = 1.0
    store().put_pattern(p)
    with pytest.raises(pat.PatternError) as e:
        pat.approve(p.id, "eli")                                              # integration still pending
    assert e.value.status == 409
    integrations.decide(i["id"], "eli", True)
    assert pat.approve(p.id, "eli").status == PatternStatus.approved
    integrations.decide(i["id"], "eli", False)
    p = store().get_pattern(p.id)
    assert p.status == PatternStatus.shadow and "rejected" in p.alert


def test_integration_artifacts_deploy_into_the_restricted_vpc(fresh_db, monkeypatch):
    from app.deploy import DeployError, deploy

    p = Pattern(name="r", kind="ml", model_uri=str(Path(settings.data_dir)), integration_ids=["int_x"])
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    (Path(settings.data_dir) / "model.onnx").write_bytes(b"x")
    monkeypatch.setattr(settings, "artifact_runtime", "lambda")
    monkeypatch.setattr(settings, "artifact_role_arn", "arn:aws:iam::123456789012:role/a")
    monkeypatch.setattr(settings, "ml_runtime_image", "img")

    class Client:
        class exceptions:
            ResourceNotFoundException = KeyError
        created = {}

        def get_function(self, **kw):
            raise KeyError

        def create_function(self, **kw):
            Client.created = kw
    with pytest.raises(DeployError):
        deploy(p, client=Client())                                            # no restricted network configured
    monkeypatch.setattr(settings, "artifact_subnet_ids", "subnet-a,subnet-b")
    monkeypatch.setattr(settings, "artifact_integration_sg", "sg-1")
    deploy(p, client=Client())
    assert Client.created["VpcConfig"] == {"SubnetIds": ["subnet-a", "subnet-b"], "SecurityGroupIds": ["sg-1"]}


@pytest.mark.integration                       # needs a localhost HTTP server
def test_integration_calls_never_follow_redirects(fresh_db):
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://example.com/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        i = integrations.register("ben", "x", "http", f"http://127.0.0.1:{srv.server_port}/t", "feature_source")
        integrations.decide(i["id"], "eli", True)
        with pytest.raises(integrations.IntegrationError):
            integrations.call(i["id"], {"email": "a@example.com"})
    finally:
        srv.shutdown()
