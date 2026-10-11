"""The scripted agent LLM (BUILD.md §17.1, CASCADE_LLM=demo / CASCADE_AGENT_LLM=scripted).

A deterministic tool-call script drives the agent; all the training and evaluation it triggers is real.
The Demo LLM answers `purpose="agent"` requests with the next tool call (as JSON), so every agent decision
still costs real, ledger-recorded tokens. With Bedrock configured, Claude drives the same tools instead.

  classification / routing:  it.1 rule (keywords) -> it.2 ml (TF-IDF unigrams + logistic regression)
                             -> it.3+ ml retrained per feedback (bigrams, balanced classes, tuned C)
  scoring:                   dl (small transformer through the training-job interface)
  lookup:                    extract_table from the attached documents -> rule over the table
  extraction / transform:    rule written by the model
The user can steer: "go straight to ml" / "deep-learning" in the instructions skips the rule.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..heavy import LLMRequest

FEEDBACK_CLASS_WORDS = ("refund", "returns", "billing", "shipping", "technical", "account")


def _plan(state: dict[str, Any]) -> list[dict[str, Any]]:
    task = state.get("task_type")
    it = int(state.get("iteration", 1))
    instructions = str(state.get("intake", {}).get("instructions", "")).lower()
    feedback = " ".join(str(f) for f in state.get("feedback", [])).lower()
    wants_dl = "deep" in instructions or "dl " in instructions or "transformer" in instructions
    wants_ml = "straight to ml" in instructions or "skip the rule" in instructions
    rebuild: list[dict[str, Any]] = [{"tool": "build_dataset", "args": {}}] if it > 1 else []   # new data / feedback
    eval_last: list[dict[str, Any]] = [{"tool": "evaluate", "args": {"candidate_id": "$last"}},
                                       {"tool": "inspect_errors", "args": {"candidate_id": "$last", "k": 8}}]
    if state.get("intake", {}).get("source_code"):            # the workspace editor's "Train" on edited code
        return [{"tool": "build_dataset", "args": {}}, {"tool": "train_source", "args": {}}, *eval_last,
                {"tool": "report", "args": {"recommendation": "accept"}}]
    if task == "lookup":
        return [{"tool": "get_pattern_summary", "args": {}},
                {"tool": "extract_table", "args": {"document_ids": state.get("intake", {}).get("document_ids", []),
                                                   "schema": "vacation days by country"}},
                {"tool": "write_lookup_rule", "args": {}},
                {"tool": "evaluate", "args": {"candidate_id": "$last"}},
                {"tool": "report", "args": {"recommendation": "accept"}}]
    if task == "scoring" or wants_dl:
        if it == 1:
            return [{"tool": "get_pattern_summary", "args": {}}, {"tool": "build_dataset", "args": {}},
                    {"tool": "train_dl", "args": {"params": {"epochs": 8}}}, *eval_last,
                    {"tool": "report", "args": {"recommendation": "accept"}}]
        return [*rebuild, {"tool": "train_dl", "args": {"params": {"epochs": 12, "lr": 3e-4}}}, *eval_last,
                {"tool": "report", "args": {"recommendation": "accept"}}]
    if task in ("classification", "routing"):
        if it == 1 and not wants_ml:
            return [{"tool": "get_pattern_summary", "args": {}}, {"tool": "build_dataset", "args": {}},
                    {"tool": "write_rule", "args": {"code": "$keyword_rule"}}, *eval_last,
                    {"tool": "report", "args": {"recommendation": "ml"}}]
        if (it == 2 and not wants_ml) or (it == 1 and wants_ml):
            pre = [{"tool": "build_dataset", "args": {}}]
            return [*pre, {"tool": "train_ml", "args": {"model": "logreg", "params": {"ngram_max": 1, "C": 1.0}}},
                    *eval_last, {"tool": "report", "args": {"recommendation": "improve_or_accept"}}]
        mentions_class = any(w in feedback for w in FEEDBACK_CLASS_WORDS)
        params = ({"ngram_max": 2, "C": 8.0, "class_weight": "balanced"} if mentions_class
                  else {"ngram_max": 2, "C": 4.0 * (it - 1)})
        return [*rebuild, {"tool": "train_ml", "args": {"model": "logreg", "params": params}}, *eval_last,
                {"tool": "report", "args": {"recommendation": "accept"}}]
    # extraction / transform: a rule the model writes
    return [{"tool": "get_pattern_summary", "args": {}}, {"tool": "build_dataset", "args": {}},
            {"tool": "write_rule", "args": {"code": "$compiled_rule"}}, *eval_last,
            {"tool": "report", "args": {"recommendation": "accept"}}]


def next_action(state: dict[str, Any]) -> dict[str, Any]:
    plan = _plan(state)
    step = int(state.get("step", 0))
    if step >= len(plan):
        return {"tool": "report", "args": {"recommendation": "accept"}}
    action: dict[str, Any] = json.loads(json.dumps(plan[step]))
    if action["args"].get("candidate_id") == "$last":
        action["args"]["candidate_id"] = state.get("last_candidate")
    return action


def scripted_reply(req: LLMRequest) -> str:
    """What the Demo LLM 'says' for an agent turn: the next tool call as JSON."""
    return json.dumps(next_action(dict(req.meta.get("state", {}))))


def report_text(state: dict[str, Any], recommendation: str) -> str:
    """The iteration report the agent writes (markdown)."""
    cand = state.get("candidates", {}).get(state.get("last_candidate") or "", {})
    m = cand.get("metrics") or {}
    kind = cand.get("kind", "?")
    it = state.get("iteration", 1)
    lines = [f"### Iteration {it}: {kind} candidate {cand.get('id', '')}", ""]
    if m:
        lines.append(f"- Agreement with the LLM on {m.get('holdout')} held-out prompts: **{m['agreement']:.1%}** "
                     f"(answers {m.get('coverage', 1):.0%} itself; the rest go to the LLM)")
        if m.get("user_label_accuracy") is not None:
            lines.append(f"- Accuracy on your labelled data: **{m['user_label_accuracy']:.1%}**")
        lines.append(f"- Latency p50 {m.get('latency_ms')} ms · size {m.get('size_mb')} MB · "
                     f"cost per request ${m.get('cost_per_request', 0):.8f}")
        worst = sorted((m.get("per_class") or {}).items(), key=lambda kv: kv[1])[:2]
        if worst:
            lines.append("- Weakest classes: " + ", ".join(f"{k} {v:.0%}" for k, v in worst))
    if cand.get("low_confidence"):
        lines.append(f"- {cand['low_confidence']} held-out prompts were answered below the confidence floor and would "
                     "go to the LLM")
    conf = cand.get("confusion") or []
    if conf:
        top = conf[0]
        lines.append(f"- Most common confusion: expected **{top['expected']}**, got **{top['got']}** "
                     f"({top['count']}×)")
    rec = {"ml": "Keyword rules don't reach the quality bar here; I recommend training an ML model next.",
           "improve_or_accept": "Good agreement. If a confusion matters to you, tell me how to treat it and I'll "
                                "retrain; otherwise accept.",
           "accept": "This candidate is ready for the verification gate. Accept to verify and send it for approval."}
    lines += ["", rec.get(recommendation, recommendation)]
    return "\n".join(lines)


def keyword_rule(state: dict[str, Any]) -> str:
    from ..demo_rules import TRIAGE_KEYWORD_RULE

    return TRIAGE_KEYWORD_RULE.strip() + "\n"


def mentions(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{w}\b", text.lower()) for w in words)
