"""Claude on Bedrock drives the agent (CASCADE_AGENT_LLM=bedrock): Converse with tool use.

The conversation is kept in the run state so a resumed run continues it. The model sees the same tools
as the scripted agent; the TOOLS enforce budget, integrations and the no-self-approval rule.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..pricing import cost
from .tools import Toolbox

TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "get_pattern_summary", "description": "Pattern stats, redacted examples, measured tokens.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "build_dataset", "description": "Merge LLM-labelled logs and user datasets into train/val/holdout.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "label_with_llm", "description": "Label more logged prompts with the LLM (overhead).",
     "schema": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}},
    {"name": "write_rule", "description": "Save a Python rule candidate defining handle(prompt) -> str.",
     "schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "train_ml", "description": "Train TF-IDF + logistic regression / gradient boosting, export ONNX.",
     "schema": {"type": "object", "properties": {"model": {"type": "string"}, "params": {"type": "object"}}}},
    {"name": "train_dl", "description": "Fine-tune a small transformer through a training job.",
     "schema": {"type": "object", "properties": {"params": {"type": "object"}}}},
    {"name": "call_integration", "description": "Call an approved integration from this run's intake.",
     "schema": {"type": "object", "properties": {"integration_id": {"type": "string"}, "payload": {"type": "object"}},
                "required": ["integration_id"]}},
    {"name": "search_knowledge", "description": "Access-filtered retrieval over the attached collections.",
     "schema": {"type": "object", "properties": {"query": {"type": "string"},
                                                 "collection_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["query"]}},
    {"name": "extract_table", "description": "Extract a fact table from documents for a lookup artifact.",
     "schema": {"type": "object", "properties": {"schema": {"type": "string"},
                                                 "document_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["schema"]}},
    {"name": "evaluate", "description": "Holdout metrics vs the LLM and user labels.",
     "schema": {"type": "object", "properties": {"candidate_id": {"type": "string"}}, "required": ["candidate_id"]}},
    {"name": "inspect_errors", "description": "The worst disagreements of a candidate.",
     "schema": {"type": "object", "properties": {"candidate_id": {"type": "string"}, "k": {"type": "integer"}},
                "required": ["candidate_id"]}},
    {"name": "report", "description": "Write the iteration report and pause for the user.",
     "schema": {"type": "object", "properties": {"recommendation": {"type": "string"}}, "required": ["recommendation"]}},
    {"name": "ask_user", "description": "Pause with a specific question for the user.",
     "schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]


def tool_config() -> dict[str, Any]:
    return {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                    "inputSchema": {"json": t["schema"]}}} for t in TOOL_SPECS]}


def next_action(run: dict[str, Any], tb: Toolbox, client: Any = None) -> dict[str, Any]:
    if client is None:
        from ..heavy import bedrock_client

        client = bedrock_client()
    convo: list[dict[str, Any]] = run.setdefault("conversation", [])
    if not convo:
        convo.append({"role": "user", "content": [{"text": json.dumps({
            "pattern": tb.get_pattern_summary(), "intake": run["intake"], "budget": run["budget"]})}]})
    elif run.get("pending_tool_use"):
        # every toolUse is answered with a toolResult carrying the same id: the tool's result, its error,
        # or (after report / ask_user) what the user said
        tid = run["pending_tool_use"]
        ev = next((e for e in reversed(run.get("events", [])) if e.get("tool_use_id") == tid), None)
        if ev is not None and ev["kind"] == "tool":
            content: list[dict[str, Any]] = [{"json": {"result": ev.get("result")}}]
            status = "success"
        elif ev is not None and ev["kind"] == "error":
            content, status = [{"text": ev["text"]}], "error"
        else:
            note = f"The user responded: {run['feedback'][-1]}" if run.get("feedback") else "The user responded."
            content, status = [{"text": note}], "success"
        convo.append({"role": "user", "content": [{"toolResult": {"toolUseId": tid, "content": content,
                                                                  "status": status}}]})
        run["pending_tool_use"] = None
    resp = client.converse(modelId=settings.bedrock_model_id, messages=convo, toolConfig=tool_config(),
                           system=[{"text": "You are Cascade's model-building agent. Build the cheapest artifact "
                                            "(rule, ml, dl) that matches the LLM; report after each candidate."}],
                           inferenceConfig={"maxTokens": 2048, "temperature": 0})
    usage = resp.get("usage", {})
    tb.charge(cost(settings.bedrock_model_id, int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))),
              "agent", int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0)), settings.bedrock_model_id)
    msg = resp["output"]["message"]
    convo.append(msg)
    for block in msg["content"]:
        if "toolUse" in block:
            tu = block["toolUse"]
            return {"tool": tu["name"], "args": tu.get("input", {}), "tool_use_id": tu["toolUseId"]}
    text = "".join(b.get("text", "") for b in msg["content"])
    return {"tool": "report", "args": {"recommendation": text or "accept"}}
