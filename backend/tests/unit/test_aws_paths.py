"""AWS code paths, tested with moto / botocore Stubber (never real AWS outside `make test-aws`)."""
import io
import json
import zipfile

import boto3
import pytest
from botocore.stub import ANY, Stubber
from moto import mock_aws

from app import deploy, engine
from app import patterns as pat
from app.config import settings
from app.heavy import BedrockHeavyModel, ContextBlock, LLMRequest, set_heavy_model
from app.runtime import LambdaRuntime, set_runtime
from app.store import store
from tests.helpers import install

EXTRACT = "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'"
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                             "Action": "sts:AssumeRole"}]})


@pytest.fixture
def aws_lambda(fresh_db, monkeypatch):
    with mock_aws():
        iam = boto3.client("iam", region_name="us-east-1")
        role = iam.create_role(RoleName="cascade-artifact", AssumeRolePolicyDocument=TRUST)["Role"]["Arn"]
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="cascade-artifacts")
        s3.put_bucket_versioning(Bucket="cascade-artifacts", VersioningConfiguration={"Status": "Enabled"})
        monkeypatch.setattr(settings, "artifact_role_arn", role)
        monkeypatch.setattr(settings, "artifact_bucket", "cascade-artifacts")
        yield boto3.client("lambda", region_name="us-east-1"), s3


def _functions(client):
    return {f["FunctionName"] for f in client.list_functions()["Functions"]}


@pytest.mark.acceptance("P1-09")
def test_approve_deploys_lambda_and_edit_reject_tear_down(aws_lambda, monkeypatch):
    client, s3 = aws_lambda
    p = install("extraction")                  # compiled + verified with the local runtime
    monkeypatch.setattr(settings, "artifact_runtime", "lambda")
    # approving (again, after an edit) goes through the Lambda API
    p = pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# reviewed\n"))
    assert p.status.value == "draft" and p.version == 2
    monkeypatch.setattr(settings, "artifact_runtime", "subprocess")
    assert pat.verify_pattern(p.id).passed
    monkeypatch.setattr(settings, "artifact_runtime", "lambda")
    p = pat.approve(p.id, "eli")
    name = f"cascade-artifact-{p.id}"
    assert p.status.value == "approved" and p.deployed_function == name
    fn = client.get_function(FunctionName=name)["Configuration"]
    assert fn["Runtime"] == "python3.12" and fn["Handler"] == "lambda_function.lambda_handler"
    assert fn["Timeout"] == 5 and fn["MemorySize"] == 256 and fn["Role"] == settings.artifact_role_arn
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="cascade-artifacts")["Contents"]]
    assert f"artifacts/{p.id}/v2/handler.py" in keys

    # matching prompts are then served by the Lambda (invoke stubbed: moto would need Docker)
    rt_client = boto3.client("lambda", region_name="us-east-1")
    stub = Stubber(rt_client)
    payload = json.dumps({"result": '{"amount": "84.90"}', "confidence": 1.0}).encode()
    stub.add_response("invoke", {"StatusCode": 200, "Payload": io.BytesIO(payload)},
                      {"FunctionName": name, "InvocationType": "RequestResponse", "Payload": ANY})
    stub.activate()
    set_runtime("lambda", LambdaRuntime(rt_client))
    r = engine.route(EXTRACT, "ava")
    assert r.route.value == "artifact" and r.answer == '{"amount": "84.90"}'
    stub.assert_no_pending_responses()

    # editing an approved pattern tears its Lambda down and sends it back to draft
    p = pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n# v3\n"))
    assert p.status.value == "draft" and name not in _functions(client)

    # rejecting an approved pattern tears it down too
    monkeypatch.setattr(settings, "artifact_runtime", "subprocess")
    assert pat.verify_pattern(p.id).passed
    monkeypatch.setattr(settings, "artifact_runtime", "lambda")
    pat.approve(p.id, "eli")
    assert name in _functions(client)
    pat.reject(p.id)
    assert name not in _functions(client) and store().get_pattern(p.id).status.value == "rejected"
    deploy.teardown(p.id)                      # NotFound is ignored


def test_lambda_zip_runs_the_handler(fresh_db, tmp_path):
    p = install("conversion")
    from app.runtime import artifact_dir

    data = deploy.build_zip(artifact_dir(p))
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp_path)
    import importlib.util
    import subprocess
    import sys

    code = ("import lambda_function, json; print(json.dumps(lambda_function.lambda_handler("
            "{'prompt': 'Convert 12 km to miles'}, None)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"result": "12 km = 7.46 miles", "confidence": 1.0}
    assert importlib.util  # the handler ran in its own process, never in the test process


def test_failed_deploy_does_not_approve(fresh_db, monkeypatch):
    p = install("conversion")
    p = pat.edit(p.id, pat.PatternEdit(handler_code=p.handler_code + "\n"))
    assert pat.verify_pattern(p.id).passed
    monkeypatch.setattr(settings, "artifact_runtime", "lambda")
    monkeypatch.setattr(settings, "artifact_role_arn", "")
    with mock_aws():
        with pytest.raises(pat.PatternError) as e:
            pat.approve(p.id, "eli")
    assert e.value.status == 502 and store().get_pattern(p.id).status.value == "draft"


def test_approve_requires_verification(fresh_db):
    from app.schemas import CompileRequest
    from tests.helpers import examples

    p = pat.compile_pattern(CompileRequest(name="x", description="x", task_type="transform", examples=examples("conversion")))
    with pytest.raises(pat.PatternError) as e:
        pat.approve(p.id, "eli")
    assert e.value.status == 409


# ------------------------------------------------------------------------- Bedrock Converse (P1-10)
CONVERSE_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "Employees in France get 25 vacation days [1]."}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 900, "outputTokens": 21, "totalTokens": 1421, "cacheReadInputTokens": 500,
              "cacheWriteInputTokens": 0},
    "metrics": {"latencyMs": 812},
}


@pytest.mark.acceptance("P1-10")
def test_bedrock_converse_request_and_usage(fresh_db):
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    m = BedrockHeavyModel(client=client, model_id="anthropic.claude-sonnet-4-5-20250929-v1:0")
    req = LLMRequest(question="and for Spain?", context=[ContextBlock(1, "Employee Handbook", "France | 25")],
                     history=[("assistant", "orphan"), ("user", "How many vacation days in France?"),
                              ("assistant", "25 days.")], knowledge=True)
    body = m.build_request(req)
    assert body["system"][1] == {"cachePoint": {"type": "default"}}            # stable prefix is cacheable
    assert "[1] Employee Handbook" in body["system"][2]["text"]
    assert [x["role"] for x in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][-1]["content"][0]["text"] == "and for Spain?"
    stub = Stubber(client)
    stub.add_response("converse", CONVERSE_RESPONSE, body)
    with stub:
        res = m.complete(req)
    assert res.text.startswith("Employees in France") and res.output_tokens == 21
    assert res.input_tokens == 1400 and res.cache_read_tokens == 500
    assert sum(res.split.values()) == 1400


def test_bedrock_route_records_real_usage(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "llm", "bedrock")
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    stub = Stubber(client)
    resp = dict(CONVERSE_RESPONSE, usage={"inputTokens": 37, "outputTokens": 120, "totalTokens": 157})
    stub.add_response("converse", resp)
    set_heavy_model(BedrockHeavyModel(client=client))
    with stub:
        r = engine.route("Write a haiku about hiking boots", "ava")
    assert r.route.value == "llm" and r.tokens.input_tokens == 37 and r.tokens.output_tokens == 120
    assert r.model_id == settings.bedrock_model_id


@pytest.mark.aws
@pytest.mark.acceptance("P1-10")
def test_real_bedrock_answers_with_usage(fresh_db, monkeypatch):  # pragma: no cover - make test-aws only
    monkeypatch.setattr(settings, "llm", "bedrock")
    set_heavy_model(None)
    r = engine.route("In one sentence, what is a tent footprint?", "ava")
    assert r.route.value == "llm" and r.tokens.input_tokens > 0 and r.tokens.output_tokens > 0
