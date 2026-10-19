"""Compile and verify against a real LLM (found by `make demo-measure` on Bedrock): redacted examples keep the
shape of identifiers, the rule and the reference answer share one output format per task type, the comparators
accept a real model's formatting, and the cachePoint is only sent to models that accept it."""
import boto3
import pytest

from app import patterns as pat
from app import privacy, verifier
from app.config import settings
from app.heavy import BedrockHeavyModel, LLMRequest, LLMResult, parse_json_reply, supports_prompt_cache
from app.schemas import CompileRequest, Pattern, TaskType

RULE = 'def handle(prompt: str) -> str:\n    return "__FALLBACK__"\n'


class Recorder:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        return LLMResult(text=self.text, input_tokens=10, output_tokens=5, model_id="demo-llm")


def test_redact_shapes_keeps_the_format_of_identifiers_only():
    out = privacy.redact_shapes("Refund $470.99 on order NP-54522 to sofia@example.com")
    assert out == "Refund $470.99 on order <ID:AA-99999> to <EMAIL>"
    assert privacy.shape("Ab-12x") == "Aa-99a"


def test_compile_prompt_explains_placeholders_and_asks_for_the_output_format(fresh_db, monkeypatch):
    llm = Recorder(f"```python\n{RULE}```")
    monkeypatch.setattr(pat, "heavy_model", lambda: llm)
    ex = ["Extract the order number, email and amount: 'Refund $4.00 on order NP-54522 to sofia@example.com'"]
    p = pat.compile_pattern(CompileRequest(name="x", description="x", task_type="extraction", examples=ex))
    req = llm.requests[0]
    assert "<ID:AA-99999>" in req.question and "NP-54522" not in req.question and "sofia@" not in req.question
    assert verifier.OUTPUT_FORMATS["extraction"] in req.question
    assert "placeholder" in req.system and req.max_tokens == settings.compile_max_tokens
    assert p.handler_code == RULE


def test_unclosed_code_fence_is_still_extracted():
    assert pat._extract_code(f"```python\n{RULE}") == RULE


def test_reference_answer_is_asked_for_in_the_task_format(fresh_db, monkeypatch):
    llm = Recorder('{"order_number": "NP-1"}')
    monkeypatch.setattr(verifier, "heavy_model", lambda: llm)
    p = Pattern(name="x", description="x", task_type=TaskType.extraction)
    assert verifier.llm_reference(p, "verify")("q").answer == '{"order_number": "NP-1"}'
    assert llm.requests[0].system.endswith(verifier.OUTPUT_FORMATS["extraction"])
    assert verifier.reference_system(Pattern(name="y", description="y", task_type=TaskType.lookup)) \
        == LLMRequest("q").system


def test_comparators_accept_a_real_models_formatting():
    fenced = '```json\n{\n  "order_number": "NP-1",\n  "amount": "€2.00"\n}\n```'
    assert verifier.agree_json('{"order_number": "NP-1", "amount": "\\u20ac2.00"}', fenced) == 1.0
    assert verifier.agree_transform("0.3 m", "0.30 m") == 1.0
    assert verifier.agree_transform("16.4 feet", "16.40 feet.") == 1.0
    assert verifier.agree_transform("16.4 feet", "16.5 feet") == 0.0
    assert verifier.comparator("transform")[1] is verifier.agree_transform


def test_json_replies_are_read_through_fences_and_surrounding_text():
    assert parse_json_reply('{"a": 1}') == {"a": 1}
    assert parse_json_reply('```json\n{"Germany": "30"}\n```') == {"Germany": "30"}
    assert parse_json_reply('Here is the table:\n{"France": 25} [1]') == {"France": 25}
    with pytest.raises(ValueError):
        parse_json_reply("no json here")


def test_scripted_agent_never_asks_a_real_llm_to_play_the_script(monkeypatch):
    from app.agent import runner, scripted

    def no_llm():
        raise AssertionError("a real LLM was asked for a scripted agent turn")

    monkeypatch.setattr(settings, "llm", "bedrock")
    monkeypatch.setattr(settings, "agent_llm", "scripted")
    monkeypatch.setattr(runner, "heavy_model", no_llm)
    run = {"id": "run_1", "task_type": "scoring", "iteration": 1, "step": 0, "intake": {}, "feedback": [],
           "last_candidate": None}
    view = {k: run[k] for k in ("task_type", "iteration", "step", "intake", "feedback", "last_candidate")}
    assert runner._brain_action(run, None) == scripted.next_action(view)  # type: ignore[arg-type]


def test_cache_point_only_for_models_that_support_it():
    assert supports_prompt_cache("eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert supports_prompt_cache("eu.amazon.nova-pro-v1:0")
    assert not supports_prompt_cache("eu.mistral.pixtral-large-2502-v1:0")
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    body = BedrockHeavyModel(client=client, model_id="eu.mistral.pixtral-large-2502-v1:0").build_request(
        LLMRequest("q"))
    assert all("cachePoint" not in b for b in body["system"])
