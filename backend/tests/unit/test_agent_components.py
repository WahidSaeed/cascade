"""Agent components: API, datasets, integrations, Bedrock tool use, tools' guards."""
import io
import json
import random
import time
import zipfile

import boto3
import pytest
from botocore.stub import ANY, Stubber

from app import demo_world as w
from app import engine, seed
from app.agent import bedrock_agent, datasets, integrations, runner, scripted, tools
from app.config import settings
from app.discovery import discover
from app.store import store


@pytest.fixture
def triage(fresh_db, monkeypatch):
    seed.seed_users()
    monkeypatch.setattr(settings, "discovery_min", 25)
    rng = random.Random(11)
    for i in range(260):
        engine.route(w.make_triage_prompt(rng, w.TRIAGE_CLASSES[i % 6])[0], "ben")
    return next(p for p in discover() if p.name == "Ticket triage")


def test_run_api_end_to_end(triage, client, monkeypatch):
    monkeypatch.setattr(settings, "agent_executor", "inline")
    hdr = {"X-User-Id": "ava"}
    up = client.post("/api/datasets/upload-url", json={"filename": "t.csv"}, headers=hdr).json()
    csv = open(f"{settings.demo_dir}/data/labelled_tickets.csv", "rb").read()
    assert client.put(up["url"], content=csv).status_code == 200
    ds = client.post("/api/datasets", json={"filename": "t.csv", "key": up["key"],
                                             "columns_map": {"input": "ticket_text", "label": "label"}}, headers=hdr).json()
    assert ds["rows"] == 300 and client.get(f"/api/datasets/{ds['id']}", headers=hdr).json()["use"] == "both"
    assert client.post("/api/datasets", json={"filename": "t.csv", "key": up["key"], "columns_map": {"input": "x"}},
                       headers={"X-User-Id": "ben"}).status_code == 403          # someone else's upload
    run = client.post("/api/agent/runs", json={"pattern_id": triage.id, "dataset_ids": [ds["id"]],
                                                "instructions": "go straight to ml", "start": True}, headers=hdr).json()
    for _ in range(200):
        run = client.get(f"/api/agent/runs/{run['id']}").json()
        if run["status"] == "awaiting_feedback":
            break
        time.sleep(0.1)
    assert run["iterations"][-1]["candidate"]["kind"] == "ml"
    assert client.get("/api/agent/runs", params={"pattern_id": triage.id}).json()[0]["id"] == run["id"]
    assert client.post(f"/api/agent/runs/{run['id']}/budget", json={"max_cost_usd": 9}, headers=hdr).json()["budget"]["max_cost_usd"] == 9
    assert client.post(f"/api/agent/runs/{run['id']}/respond", json={"action": "bogus"}, headers=hdr).status_code == 422
    stopped = client.post(f"/api/agent/runs/{run['id']}/respond", json={"action": "stop"}, headers=hdr).json()
    assert stopped["status"] == "stopped"
    body = client.get(f"/api/agent/runs/{run['id']}/events?after=0").text      # a finished run's stream ends
    steps = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:") and '"kind"' in line]
    assert steps[0]["n"] == 1 and steps[-1]["kind"] == "stop" and "event: status" in body
    assert client.post(f"/api/agent/runs/{run['id']}/respond", json={"action": "stop"}, headers=hdr).status_code == 409
    assert client.get("/api/agent/runs/run_missing").status_code == 404
    assert client.get("/api/patterns/missing/intake").status_code == 404


def test_add_input_reopens_and_continues(triage):
    csv = open(f"{settings.demo_dir}/data/labelled_tickets.csv", "rb").read()
    train = datasets.register("ava", "t.csv", {"input": "ticket_text", "label": "label"}, "train", data=csv)
    run = runner.create_run(triage.id, "ava", {"instructions": "go straight to ml", "dataset_ids": [train["id"]]})
    run = runner.start_run(run["id"], sync=True)
    assert run["status"] == "awaiting_feedback" and run["iterations"][-1]["metrics"]["user_label_accuracy"] is None
    ev = datasets.register("ava", "e.csv", {"input": "ticket_text", "label": "label"}, "eval", data=csv)
    run = runner.respond(run["id"], "add_input", "", "ava", sync=True, intake={"dataset_ids": [ev["id"]]})
    assert ev["id"] in run["intake"]["dataset_ids"] and run["iteration"] == 2
    assert run["iterations"][-1]["metrics"]["user_label_accuracy"] is not None      # the new data is used


def test_gate_refuses_a_weak_candidate(triage):
    run = runner.create_run(triage.id, "ava", {})
    run = runner.start_run(run["id"], sync=True)                    # iteration 1: the keyword rule (< 80 %)
    run = runner.respond(run["id"], "accept", "", "ava")
    assert run["status"] == "awaiting_feedback" and "gate failed" in run["events"][-1]["text"]
    assert store().get_pattern(triage.id).status.value != "approved"


def test_tools_guards(triage, monkeypatch):
    run = runner.create_run(triage.id, "ava", {})
    tb = tools.Toolbox(run["id"], triage, runner.load(run["id"]), "ava")
    with pytest.raises(tools.ToolError):
        tb.train_ml()                                                # no dataset yet
    tb.build_dataset()
    monkeypatch.setattr(settings, "dl_min_samples", 10_000)
    with pytest.raises(tools.ToolError):
        tb.train_dl()
    with pytest.raises(tools.ToolError):
        tb.evaluate("c99")
    with pytest.raises(tools.ToolError):
        tb.inspect_errors("")
    tb.state["budget"]["max_cost_usd"] = 0.0
    with pytest.raises(tools.BudgetExceeded):
        tb.label_with_llm(3)
    tb.state["budget"]["max_cost_usd"] = 1.0
    out = tb.label_with_llm(2)
    assert out["labelled"] >= 0
    with pytest.raises(tools.ToolError):
        runner._call_tool(tb, "approve", {}, tb.state)               # approval is never a tool
    assert tools.wrap_like("Classify this support ticket: 'x'", "Rate the urgency 1-5: 'late boots'") \
        == "Rate the urgency 1-5: 'late boots'"
    assert tools.strip_prefix("priority-billing", {"map": {"gold": "priority-"}}) == "billing"
    assert tools.prompt_prefixes(["Classify this support ticket: 'a'"] * 50 + ["Which category is this ticket? 'b'"] * 50) \
        == ["classify this suppor", "which category is th"]


def test_knowledge_tools_respect_access(fresh_db):
    seed.seed_users()
    docs = seed.seed_knowledge()
    from app.schemas import Pattern, TaskType

    p = Pattern(name="lookup", task_type=TaskType.lookup)
    store().put_pattern(p)
    run = runner.create_run(p.id, "dana", {"collection_ids": [seed.collection_id("handbook"), seed.collection_id("compensation")]})
    tb = tools.Toolbox(run["id"], p, runner.load(run["id"]), "dana")
    ctx = tb.search_knowledge("bonus range for team leads", [seed.collection_id("compensation"), "col_other"])
    assert all("bonus" not in c["text"].lower() for c in ctx)       # Dana can't read compensation
    with pytest.raises(tools.ToolError):
        tb.extract_table([docs["compensation_guide"]], "pay bands")
    out = tb.extract_table([docs["handbook_v1"]], "vacation days by country")
    assert out["rows"] == 10 and "Employee Handbook v1" in out["dependencies"]


def test_lookup_plan_via_agent(fresh_db):
    seed.seed_users()
    docs = seed.seed_knowledge()
    pid = seed.install_vacation_lookup(docs["handbook_v1"], approver=None)
    run = runner.create_run(pid, "chen", {"document_ids": [docs["handbook_v1"]]})
    run = runner.start_run(run["id"], sync=True)
    assert run["iterations"][-1]["metrics"]["agreement"] == 1.0
    run = runner.respond(run["id"], "accept", "", "chen")
    assert run["status"] == "accepted" and store().get_pattern(pid).kb_dependencies


def test_scripted_plans():
    s = {"task_type": "scoring", "iteration": 2, "step": 0}
    assert scripted.next_action(s)["tool"] == "build_dataset" and scripted.next_action({**s, "step": 1})["tool"] == "train_dl"
    s = {"task_type": "classification", "iteration": 1, "step": 0, "intake": {"instructions": "use a transformer"}}
    assert scripted.next_action({**s, "step": 2})["tool"] == "train_dl"
    s = {"task_type": "extraction", "iteration": 1, "step": 2}
    assert scripted.next_action(s)["args"]["code"] == "$compiled_rule"
    assert scripted.next_action({"task_type": "classification", "iteration": 4, "step": 99})["tool"] == "report"
    assert scripted.next_action({"task_type": "classification", "iteration": 4, "step": 1, "feedback": []})["args"][
        "params"]["C"] == 12.0
    assert scripted.mentions("treat refund separately", ("refund",))


def test_integrations_module_scan_and_validation(fresh_db):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tiers/requirements.txt", "requests==2.32\n")
        z.writestr("tiers/core.py", "import subprocess\nsubprocess.run(['ls'])\n")
    i = integrations.register("ben", "Tier module", "module", "tiers.core", "pre_post", module=buf.getvalue())
    assert "requests==2.32" in i["scan"]["dependencies"] and any("subprocess" in f for f in i["scan"]["findings"])
    with pytest.raises(integrations.IntegrationError):
        integrations.call(i["id"], {})                                # pending
    integrations.decide(i["id"], "eli", True)
    with pytest.raises(integrations.IntegrationError):
        integrations.call(i["id"], {})                                # modules aren't HTTP-callable
    with pytest.raises(integrations.IntegrationError):
        integrations.register("ben", "x", "http", "ftp://nope", "feature_source")
    with pytest.raises(integrations.IntegrationError):
        integrations.register("ben", "x", "aws", "not-an-arn", "core_model")
    with pytest.raises(integrations.IntegrationError):
        integrations.register("ben", "x", "ftp", "x", "feature_source")
    s = integrations.register("ben", "With secret", "http", "http://127.0.0.1:9/x", "feature_source", secret="tok")
    assert s["has_secret"] and integrations._secret(f"local:cascade/integrations/{s['id']}") == "tok"
    assert integrations.list_all("pending")[0]["id"] == s["id"]
    assert integrations.decide(s["id"], "eli", False)["status"] == "rejected"


def test_bedrock_agent_tool_use(triage):
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    stub = Stubber(client)
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "build_dataset", "input": {}}}]}}, "stopReason": "tool_use",
        "usage": {"inputTokens": 900, "outputTokens": 40, "totalTokens": 940}, "metrics": {"latencyMs": 10}},
        {"modelId": ANY, "messages": ANY, "toolConfig": ANY, "system": ANY, "inferenceConfig": ANY})
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "accept"}]}},
                                   "stopReason": "end_turn", "usage": {"inputTokens": 1000, "outputTokens": 5,
                                                                       "totalTokens": 1005}, "metrics": {"latencyMs": 10}},
                      {"modelId": ANY, "messages": ANY, "toolConfig": ANY, "system": ANY, "inferenceConfig": ANY})
    run = runner.load(runner.create_run(triage.id, "ava", {})["id"])
    tb = tools.Toolbox(run["id"], triage, run, "ava")
    with stub:
        a = bedrock_agent.next_action(run, tb, client)
        assert a == {"tool": "build_dataset", "args": {}, "tool_use_id": "t1"}
        run.setdefault("events", []).append({"n": 1, "kind": "tool", "result": {"train": 1}, "tool_use_id": "t1"})
        b = bedrock_agent.next_action(run, tb, client)
    assert b["tool"] == "report" and tb.spent() > 0                     # agent tokens are overhead
    names = {t["toolSpec"]["name"] for t in bedrock_agent.tool_config()["tools"]}
    assert "approve" not in names and "deploy" not in names and "report" in names
