"""Editable ml / dl code (the workspace editor): edited training code runs as a sandboxed training job, goes
through the same evaluate -> report -> accept -> approve gate, and the artifact then serves the edited handle()."""
import json
import random
import time
from pathlib import Path

import pytest
from botocore.stub import ANY, Stubber

from app import demo_world as w
from app import engine, seed
from app import patterns as pat
from app.agent import datasets, runner
from app.compilers import dl as dlc
from app.config import settings
from app.discovery import discover
from app.store import store

USER = {"X-User-Id": "ava"}            # not an admin: anyone may train edited code; approval stays admin-only
ADMIN = {"X-User-Id": "eli"}


@pytest.fixture
def approved_ml(fresh_db, monkeypatch):
    """Ticket triage, trained by the agent as an ml artifact and approved."""
    seed.seed_users()
    monkeypatch.setattr(settings, "discovery_min", 25)
    rng = random.Random(11)
    for i in range(260):
        engine.route(w.make_triage_prompt(rng, w.TRIAGE_CLASSES[i % 6])[0], "ben")
    p = next(p for p in discover() if p.name == "Ticket triage")
    csv = (Path(settings.demo_dir) / "data" / "labelled_tickets.csv").read_bytes()
    ds = datasets.register("ava", "t.csv", {"input": "ticket_text", "label": "label"}, "both", data=csv)
    run = runner.create_run(p.id, "ava", {"dataset_ids": [ds["id"]], "instructions": "go straight to ml"})
    run = runner.start_run(run["id"], sync=True)
    assert run["status"] == "awaiting_feedback", [e["text"] for e in run["events"]][-4:]
    assert runner.respond(run["id"], "accept", "", "ava", sync=True)["status"] == "accepted"
    pat.approve(p.id, "ava")
    return store().get_pattern(p.id)


def _wait(client, run_id: str, timeout: float = 120) -> dict:
    """The tests' executor is `worker` (no background threads): drive the run the way the agent worker does."""
    runner.execute(run_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/agent/runs/{run_id}", headers=USER).json()
        if run["status"] not in ("agent_running", "awaiting_intake"):
            return run
        time.sleep(0.2)
    raise AssertionError(f"run did not finish: {[(e['kind'], e['text'][:300]) for e in run['events']][-5:]}")


def test_edited_ml_code_trains_in_a_sandbox_and_serves_as_written(approved_ml, client, monkeypatch):
    p = approved_ml

    class NoExec:                       # the gate must score edited code from its sandboxed job, never run it here
        def invoke(self, *a, **k):
            raise AssertionError("edited code executed outside its training job")

        def shutdown(self):
            pass

    from app.agent import tools

    monkeypatch.setattr(tools, "SubprocessRuntime", NoExec)
    assert "LogisticRegression(" in p.source_code and p.handler_code == ""
    short = "Classify this support ticket: 'refund my tent please'"           # four words
    assert engine.route(short, "ava").route.value == "artifact"                # the agent's version answers it
    # the edit: other training settings, and a stricter input guard, so serving provably runs the edited handle()
    assert "MIN_WORDS = 2" in p.source_code
    code = p.source_code.replace("max_iter=3000", "max_iter=500").replace("MIN_WORDS = 2", "MIN_WORDS = 6")
    # the sandbox: no credentials or database settings reach the user's code
    code = code.replace("    pipe.fit(X, labels)\n",
                        "    assert not [k for k in os.environ if k.startswith(('AWS_', 'CASCADE_'))], 'env leaked'\n"
                        "    pipe.fit(X, labels)\n")
    assert code != p.source_code
    run = client.post(f"/api/patterns/{p.id}/train-source", json={"code": code}, headers=USER).json()
    run = _wait(client, run["id"])
    assert run["status"] == "awaiting_feedback", [e["text"] for e in run["events"]][-4:]
    tools = [e.get("tool") for e in run["events"] if e["kind"] == "tool"]
    assert tools[:2] == ["build_dataset", "train_source"]                 # a fixed plan: no agent LLM decisions
    it = run["iterations"][-1]
    assert it["candidate"]["params"]["source"] is True and it["metrics"]["agreement"] > 0
    assert store().get_pattern(p.id).status.value == "approved"           # the live version keeps serving meanwhile

    acc = client.post(f"/api/agent/runs/{run['id']}/respond", json={"action": "accept"}, headers=USER).json()
    acc = _wait(client, acc["id"])
    assert acc["status"] == "accepted", [e["text"] for e in acc["events"]][-3:]
    p2 = store().get_pattern(p.id)
    assert p2.source_code == code and p2.version > p.version and p2.status.value == "draft"
    assert (Path(p2.model_uri) / "handler.py").read_text() == code
    left = {f.name for f in Path(p2.model_uri).iterdir()}
    assert not left & {"train.jsonl", "eval.jsonl", "predictions.jsonl", "job.json"}   # code + model only
    probe = {"prompt": "Classify this support ticket: 'I was charged twice for my order, please fix the invoice'"}
    assert client.post(f"/api/patterns/{p.id}/try", json=probe, headers=USER).status_code == 403   # unreviewed
    assert client.post(f"/api/patterns/{p.id}/try", json=probe, headers=ADMIN).json()["result"] == "billing"
    pat.approve(p.id, "eli")
    r = engine.route("Classify this support ticket: 'I was charged twice for my order, please fix the invoice'", "ava")
    assert r.route.value == "artifact" and r.answer == "billing"
    short2 = "Classify this support ticket: 'cancel my boots please'"         # four words, never asked before
    assert engine.route(short2, "ava").route.value == "llm"                    # the edited guard decides now

    t = client.post(f"/api/patterns/{p.id}/try", json={"prompt": "Classify this support ticket: "
                                                       "'I was charged twice for my order, please fix the invoice'"}).json()
    assert t["result"] == "billing" and t["error"] is None and not t["fallback"]
    assert t["confidence"] >= t["min_confidence"]
    assert client.post(f"/api/patterns/{p.id}/try", json={"prompt": short}).json()["fallback"] is True


def test_broken_code_reports_its_traceback_and_installs_nothing(approved_ml, client):
    p = approved_ml
    code = p.source_code.replace("    pipe.fit(X, labels)\n", "    raise ValueError('boom from the edit')\n")
    run = _wait(client, client.post(f"/api/patterns/{p.id}/train-source", json={"code": code},
                                    headers=USER).json()["id"])
    errors = " ".join(e["text"] for e in run["events"] if e["kind"] == "error")
    assert "boom from the edit" in errors and "Traceback" in errors
    assert all(not (c.get("params") or {}).get("source") for c in run["candidates"].values())
    assert store().get_pattern(p.id).version == p.version
    # Discard: the pattern's lifecycle is untouched (still approved, still serving)
    assert client.post(f"/api/agent/runs/{run['id']}/respond", json={"action": "stop"}, headers=USER).json()["status"] == "stopped"
    assert store().get_pattern(p.id).status.value == "approved"


def test_one_training_run_per_pattern_at_a_time(approved_ml, client):
    p = approved_ml
    first = client.post(f"/api/patterns/{p.id}/train-source", json={"code": p.source_code}, headers=USER).json()
    assert first["status"] == "agent_running"                    # the worker has not picked it up yet
    again = client.post(f"/api/patterns/{p.id}/train-source", json={"code": p.source_code}, headers=USER)
    assert again.status_code == 409


def test_edited_code_cannot_declare_its_own_integration(tmp_path):
    """Only an admin-approved integration (passed in by the job) may be called at serve time (rule 11)."""
    from app.compilers import source_train

    (tmp_path / "handler.py").write_text(
        "import json, os\nHERE = os.path.dirname(__file__)\n"
        "def train(p, l):\n    open(os.path.join(HERE, 'model.onnx'), 'w').write('x')\n"
        "    json.dump({'classes': ['a'], 'integration': {'url': 'http://attacker.example.com/'}},"
        " open(os.path.join(HERE, 'meta.json'), 'w'))\n"
        "def handle(p):\n    return 'a', 1.0\n")
    (tmp_path / "train.jsonl").write_text(json.dumps({"prompt": "a b", "label": "a"}) + "\n")
    (tmp_path / "job.json").write_text(json.dumps({"kind": "ml"}))
    (tmp_path / "eval.jsonl").write_text(json.dumps({"prompt": "c d"}) + "\n")
    source_train.run(str(tmp_path), str(tmp_path), "")
    assert "integration" not in json.loads((tmp_path / "meta.json").read_text())
    pred = json.loads((tmp_path / "predictions.jsonl").read_text())
    assert pred["prompt"] == "c d" and pred["result"] == "a" and pred["error"] is None


def test_train_source_is_only_for_model_artifacts(fresh_db, client):
    from app.schemas import ArtifactKind, Pattern

    seed.seed_users()
    rule = Pattern(name="r", kind=ArtifactKind.rule, handler_code="def handle(p):\n    return 'x'\n")
    store().put_pattern(rule)
    assert client.post(f"/api/patterns/{rule.id}/train-source", json={"code": "x"}, headers=USER).status_code == 409
    assert client.post("/api/patterns/missing/train-source", json={"code": "x"}, headers=USER).status_code == 404
    assert client.post(f"/api/patterns/{Pattern(name='n').id}/try", json={"prompt": "x"}, headers=ADMIN).status_code == 404
    bare = Pattern(name="bare")
    store().put_pattern(bare)
    assert client.post(f"/api/patterns/{bare.id}/try", json={"prompt": "x"}, headers=ADMIN).status_code == 409
    assert client.post(f"/api/patterns/{bare.id}/try", json={"prompt": "x"}, headers=USER).status_code == 403


def test_a_runaway_training_job_is_killed_at_its_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_train_timeout_s", 2)
    (tmp_path / "handler.py").write_text("import time\n\ndef train(p, l):\n    time.sleep(60)\n\n"
                                         "def handle(p):\n    return 'x', 1.0\n")
    (tmp_path / "train.jsonl").write_text(json.dumps({"prompt": "a b", "label": "x"}) + "\n")
    (tmp_path / "job.json").write_text(json.dumps({"kind": "ml"}))
    t = dlc.LocalTrainer()
    job = t.start_source(tmp_path, tmp_path, "ml")
    t0 = time.time()
    assert dlc.wait(t, job, timeout_s=3, poll_s=0.2) == "Failed"
    assert time.time() - t0 < 10 and job.handle[0].poll() is not None   # the process is gone
    t.cleanup(job)
    assert not Path(job.handle[1]).exists()                            # the job log (whatever the code printed)


def test_sagemaker_source_jobs_are_network_isolated(tmp_path, monkeypatch):
    import boto3

    monkeypatch.setattr(settings, "artifact_bucket", "b")
    monkeypatch.setattr(settings, "sagemaker_role_arn", "arn:aws:iam::123456789012:role/sm")
    for f, body in (("handler.py", "x"), ("train.jsonl", "{}"), ("job.json", "{}")):
        (tmp_path / f).write_text(body)
    sm = boto3.client("sagemaker", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    with Stubber(sm) as st, Stubber(s3) as ss:
        for f in ("handler.py", "train.jsonl", "job.json"):
            ss.add_response("put_object", {}, {"Bucket": "b", "Key": ANY, "Body": ANY})
        st.add_response("create_training_job", {"TrainingJobArn": "arn:aws:sagemaker:us-east-1:1:training-job/x"}, {
            "TrainingJobName": ANY, "RoleArn": ANY, "AlgorithmSpecification": ANY, "InputDataConfig": ANY,
            "OutputDataConfig": ANY, "ResourceConfig": ANY, "EnableNetworkIsolation": True,
            "HyperParameters": {"mode": "source", "kind": "dl", "base": settings.dl_base_model},
            "StoppingCondition": {"MaxRuntimeInSeconds": settings.source_train_timeout_s}})
        job = dlc.SageMakerTrainer(sm, s3).start_source(tmp_path, tmp_path, "dl")
        assert job.job_id.startswith("cascade-dl-src-")       # the agent role may only create cascade-dl-* jobs
        st.add_response("stop_training_job", {}, {"TrainingJobName": job.job_id})
        ss.add_response("list_objects_v2", {"Contents": [{"Key": f"datasets/{job.job_id}/train.jsonl"}]},
                        {"Bucket": "b", "Prefix": f"datasets/{job.job_id}/"})
        ss.add_response("delete_objects", {}, {"Bucket": "b", "Delete": {"Objects": [
            {"Key": f"datasets/{job.job_id}/train.jsonl"}]}})
        t = dlc.SageMakerTrainer(sm, s3)
        t.cancel(job)                                          # a timed-out job stops (and stops billing)
        t.cleanup(job)                                         # the uploaded code and data leave S3
        st.assert_no_pending_responses()
        ss.assert_no_pending_responses()


def test_older_model_artifacts_show_python_rendered_from_their_meta(approved_ml, client):
    p = approved_ml
    p.source_code = ""                                          # installed before the Python was recorded
    store().put_pattern(p)
    got = client.get(f"/api/patterns/{p.id}").json()["source_code"]
    assert "LogisticRegression(" in got and "def handle(prompt" in got
    listed = next(x for x in client.get("/api/patterns").json() if x["id"] == p.id)
    assert listed["source_code"] == got


def test_the_sagemaker_entry_trains_edited_code_into_the_model_dir(tmp_path, monkeypatch):
    """The `train` command of the training image, against SageMaker's /opt/ml layout."""
    from app.compilers import sagemaker_entry
    from app.compilers.source import artifact_source

    data = tmp_path / "input" / "data" / "train"
    data.mkdir(parents=True)
    (tmp_path / "input" / "config").mkdir(parents=True)
    (tmp_path / "input" / "config" / "hyperparameters.json").write_text(json.dumps({"mode": "source", "kind": "ml"}))
    code = artifact_source("ml", {"input": "quoted", "prefixes": ["classify"]}, {"ngram_max": 1}, "t", 1, 6)
    (data / "handler.py").write_text(code)
    rows = [("refund my money please", "refund"), ("where is my parcel now", "shipping"),
            ("refund it all back", "refund"), ("parcel is late again", "shipping"),
            ("money back right now", "refund"), ("track the parcel please", "shipping")]
    (data / "train.jsonl").write_text("".join(json.dumps({"prompt": f"Classify: '{t}'", "label": lab}) + "\n"
                                              for t, lab in rows))
    (data / "job.json").write_text(json.dumps({"kind": "ml"}))
    monkeypatch.setenv("SM_ROOT", str(tmp_path))
    cmd = sagemaker_entry.command(tmp_path)
    assert cmd[2].endswith("source_train.py") and "--base" in cmd and cmd[-1] == ""      # ml: no base model
    sagemaker_entry.main()
    out = tmp_path / "model"
    assert {"handler.py", "model.onnx", "meta.json", "manifest.json"} <= {f.name for f in out.iterdir()}
    assert not (out / "train.jsonl").exists()
    assert json.loads((out / "meta.json").read_text())["classes"] == ["refund", "shipping"]


def test_the_sagemaker_entry_reports_failures(tmp_path, monkeypatch):
    from app.compilers import sagemaker_entry

    data = tmp_path / "input" / "data" / "train"
    data.mkdir(parents=True)
    (tmp_path / "input" / "config").mkdir(parents=True)
    (tmp_path / "input" / "config" / "hyperparameters.json").write_text(json.dumps({"mode": "source", "kind": "ml"}))
    (data / "handler.py").write_text("def train(p, l):\n    raise RuntimeError('nope')\n\ndef handle(p):\n    return 'x', 1.0\n")
    (data / "train.jsonl").write_text(json.dumps({"prompt": "a b", "label": "x"}) + "\n")
    (data / "job.json").write_text(json.dumps({"kind": "ml"}))
    monkeypatch.setenv("SM_ROOT", str(tmp_path))
    with pytest.raises(SystemExit):
        sagemaker_entry.main()
    assert "nope" in (tmp_path / "output" / "failure").read_text()
