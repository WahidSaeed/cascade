"""Fixes for what the live AWS test found (artifact controls, agent pipeline): each test fails without its fix."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app import patterns as pat
from app import seed
from app.agent.tools import Toolbox
from app.config import settings
from app.heavy import LLMResult, set_heavy_model
from app.runtime import readable, runtime
from app.schemas import Pattern, PatternStatus, TaskType
from app.store import store
from tests.helpers import install

ROOT = Path(__file__).resolve().parents[3]


# ----------------------------------------------------------------------------- model files in S3
@pytest.fixture
def bucket(fresh_db, monkeypatch):
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cascade-artifacts")
        monkeypatch.setattr(settings, "artifact_bucket", "cascade-artifacts")
        monkeypatch.setattr(settings, "bedrock_region", "us-east-1")
        yield


def test_model_files_reach_another_task_through_s3(bucket):
    """Training writes model files to one task's disk; approving in another task found nothing ("model files
    missing"). Published directories come back from S3 wherever they are read."""
    from app.modelstore import ensure_local, publish

    d = Path(settings.data_dir) / "models" / "pat_x" / "v2"
    d.mkdir(parents=True)
    (d / "model.onnx").write_bytes(b"\x00onnx")
    (d / "meta.json").write_text('{"classes": ["a"]}')
    publish(d)
    shutil.rmtree(d)                                      # "another task": this disk never had the files
    p = Pattern(id="pat_x", name="x", kind="ml", version=2, model_uri=str(d))
    from app.runtime import materialize

    out = materialize(p)
    assert (out / "model.onnx").read_bytes() == b"\x00onnx" and (out / "meta.json").exists()
    assert ensure_local(Path(settings.data_dir) / "models" / "nothing") == Path(settings.data_dir) / "models" / "nothing"


def test_agent_candidates_are_published(bucket):
    """A candidate the agent worker trained must be readable by the API, which runs the Accept gate."""
    p = install("conversion")
    tb = Toolbox("run_t", p, {"intake": {}, "budget": {"max_cost_usd": 1.0}}, "eli")
    cid = tb.write_rule(p.handler_code)["candidate_id"]
    keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1")
            .list_objects_v2(Bucket="cascade-artifacts")["Contents"]]
    assert f"models/agent/run_t/{cid}/handler.py" in keys


# ----------------------------------------------------------------------------- lookups verify against documents
def test_verify_button_checks_a_lookup_against_its_documents(fresh_db):
    """The Verify button called verify_pattern without a reference: the LLM answered without the handbook and a
    correct lookup could never pass. A lookup is now always checked against its documents."""
    seed.seed_users()
    docs = seed.seed_knowledge()
    pid = seed.install_vacation_lookup(docs["handbook_v1"])
    res = pat.verify_pattern(pid)                         # what POST /verify does
    assert res.passed and res.score == 1.0, res.disagreements


def test_lookup_value_reads_fenced_json():
    from app.verifier import agree_lookup, lookup_value

    assert lookup_value('```json\n{"value": "21"}\n```') == "21"
    assert lookup_value('{"value": null}') is None
    assert agree_lookup("Employees in Ireland get 21 vacation days per year.", '{"value": "21"}') == 1.0
    # what real Bedrock answered on AWS: the citation came before the fact
    cited = "According to the Employee Handbook [1], employees in Ireland receive 21 paid vacation days per year."
    assert lookup_value(cited) == "21"
    assert agree_lookup("Employees in Ireland get 21 vacation days per year.", cited) == 1.0
    assert lookup_value("Based on the context provided:\n\n[1] Employees in France receive 25 paid days.") == "25"


# ----------------------------------------------------------------------------- the Test panel on drafts
def test_try_runs_a_draft_that_has_no_function(client, fresh_db, monkeypatch):
    """With the lambda runtime a draft has no function: Run failed with "Function not found"."""
    from app import deploy

    seed.seed_users()                                     # eli is an admin: drafts run only for admins
    p = install("conversion")
    p = pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# draft\n"))
    assert p.status == PatternStatus.draft
    used = []

    class FakeCandidate:
        def __init__(self, pattern):
            used.append(pattern.id)

        def __enter__(self):
            return runtime().invoke

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(deploy, "candidate", FakeCandidate)
    r = client.post(f"/api/patterns/{p.id}/try", json={"prompt": "Convert 12 km to miles"},
                    headers={"X-User-Id": "eli"}).json()
    assert used == [p.id] and r["error"] is None and "7.46" in r["result"]


# ----------------------------------------------------------------------------- agent labels
class _Labeller:
    """A heavy model that answers in the requested output format only when one is in the system prompt."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(self, req):
        self.systems.append(req.system)
        text = "billing" if "Output format" in req.system else "This sounds like a **Billing** question because…"
        return LLMResult(text=text, input_tokens=40, output_tokens=2, model_id="demo-llm")


def test_prose_answers_are_relabelled_in_the_output_format(fresh_db):
    """Logged answers are what the LLM told users ("This sounds like a Billing question…"); training on them gave
    13% agreement. Non-label answers are re-labelled with the same format prompt verification uses."""
    p = Pattern(id="pat_r", name="routing", task_type=TaskType.routing, status=PatternStatus.recognized)
    store().put_pattern(p)
    tb = Toolbox("run_r", p, {"intake": {}, "budget": {"max_cost_usd": 1.0}}, "eli")
    fake = _Labeller()
    set_heavy_model(fake)
    try:
        rows = tb._conform([{"input": "Which queue: 'refund my order'", "label": "This is a billing issue. **Billing**",
                             "source": "llm"},
                            {"input": "Which queue: 'my parcel is late'", "label": "shipping", "source": "llm"}])
    finally:
        set_heavy_model(None)
    assert [r["label"] for r in rows] == ["billing", "shipping"]
    assert len(fake.systems) == 1 and "Output format" in fake.systems[0]   # the conforming label cost nothing


# ----------------------------------------------------------------------------- output and deployment config
def test_artifact_json_is_returned_with_real_characters():
    assert readable('{"amount": "\\u20ac84.90"}') == '{"amount": "€84.90"}'
    assert readable("7.46 miles") == "7.46 miles"
    assert readable("{not json \\u20ac") == "{not json \\u20ac"


def test_api_hands_agent_runs_to_the_worker():
    """The API ran agent runs inline with the offline scripted agent; the Bedrock worker sat idle."""
    ecs = (ROOT / "infra" / "terraform" / "ecs.tf").read_text()
    api = ecs[ecs.index('name         = "api"'):]
    assert re.search(r'CASCADE_AGENT_EXECUTOR", value = "worker"', api[:2000])


def test_verification_runs_samples_concurrently(fresh_db, monkeypatch):
    """40 sequential LLM calls exceeded CloudFront's 60 s origin timeout (504)."""
    import threading
    import time

    from app.verifier import Reference, verify

    p = install("conversion")
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow_ref(prompt):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1
        return Reference(runtime().invoke(p, prompt).result or "", None)

    res = verify(p, p.examples[:16], reference=slow_ref)
    assert res.passed and active["max"] > 1


# ----------------------------------------------------------------------------- agent worker robustness
def test_pii_screen_falls_back_to_regex_when_comprehend_is_denied(monkeypatch):
    """The agent role had no comprehend permission: write_rule raised AccessDenied on every attempt."""
    from botocore.exceptions import ClientError

    from app import privacy

    def denied(_text):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "DetectPiiEntities")

    monkeypatch.setattr(settings, "pii_backend", "comprehend")
    monkeypatch.setattr(privacy, "_comprehend", denied)
    assert privacy.contains_pii("mail me at ann@example.com") and not privacy.contains_pii("Convert 12 km")


def test_a_failing_tool_is_a_tool_error_not_an_endless_retry(fresh_db, monkeypatch):
    """An exception escaping a tool left the run at the same step; the worker re-asked the agent and crashed again,
    forever. It is now recorded as a tool error and the run moves on."""
    from app.agent import runner

    seed.seed_users()
    p = install("conversion")
    run = runner.create_run(p.id, "eli", {"max_cost_usd": 1.0})
    monkeypatch.setattr(runner, "_brain_action", lambda r, tb: {"tool": "build_dataset", "args": {}})

    def boom(*_a, **_k):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(runner, "_call_tool", boom)
    loaded = runner.load(run["id"])
    step = int(loaded.get("step", 0))
    assert runner._step(loaded, "w1") is True
    after = runner.load(loaded["id"])
    assert int(after["step"]) == step + 1 and after["events"][-1]["kind"] == "error"


def test_bedrock_clients_ride_out_throttling():
    """The account's on-demand Sonnet quota throttled 8 parallel verification calls; 4 legacy attempts failed the gate."""
    from app.heavy import bedrock_client

    retries = bedrock_client().meta.config.retries
    assert retries["mode"] == "adaptive" and retries["total_max_attempts"] >= 10


def test_every_backend_task_can_deploy_ml_and_dl_artifacts():
    """Approve in the UI (the API task) failed on AWS: "no runtime image configured for dl artifacts". Only the agent
    task had the runtime image URIs; they belong to every task that may approve or redeploy an artifact."""
    ecs = (ROOT / "infra" / "terraform" / "ecs.tf").read_text()
    shared = ecs[ecs.index("backend_env = ["):]
    shared = shared[:shared.index("\n  ]")]
    assert "CASCADE_ML_RUNTIME_IMAGE" in shared and "CASCADE_DL_RUNTIME_IMAGE" in shared


def test_agent_fine_tunes_dl_in_its_own_task():
    """The agent sent dl training to SageMaker, where a new account's training quota is 0: every dl run and every
    dl Retrain from the UI failed with ResourceLimitExceeded. It trains locally, on the image that has torch."""
    agent = (ROOT / "infra" / "terraform" / "agent.tf").read_text()
    task = agent[agent.index('resource "aws_ecs_task_definition" "agent"'):]
    task = task[:task.index("logConfiguration")]
    assert 'CASCADE_DL_TRAINER", value = "local"' in task
    assert "aws_ecr_repository.training.repository_url" in task      # the API image has no torch
