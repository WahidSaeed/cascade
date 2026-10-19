"""The agent loop (BUILD.md §11.1–11.3).

  recognized -> awaiting_intake -(Start)-> agent_running -> awaiting_feedback -(Accept)-> verifying
             -> draft (needs an admin's approval to deploy) ; or stopped / budget pause / not_compilable

Every step (one agent decision + one tool call) is checkpointed to `agent_runs.data`, and execution holds
a lease, so a killed worker's run resumes from its last step in a new task (P5-07). Caps
(`agent_max_iterations`, `agent_max_cost_usd`) pause the run and ask the user. The agent can never mark its
own work approved: Accept goes through the verification gate, and approval stays a human action (rule 10).
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update

from .. import db
from ..compilers.source import artifact_source
from ..config import settings
from ..heavy import LLMRequest, heavy_model
from ..models import AgentRunRow, Notification, new_id
from ..modelstore import ensure_local, publish
from ..pricing import cost
from ..schemas import ArtifactKind, Pattern, PatternStatus, TaskType, VerifyResult
from ..store import store
from . import integrations, scripted
from .tools import BudgetExceeded, Toolbox, ToolError

log = logging.getLogger("cascade.agent")
LEASE_S = 30.0
WORKER_ID = f"w-{uuid.uuid4().hex[:8]}"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent")
AGENT_SYSTEM = ("You are Cascade's model-building agent. Build the cheapest artifact (rule, ml, dl) that matches the "
                "LLM on this pattern. Use the tools; report after each candidate; never exceed the budget.")


class RunError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ----------------------------------------------------------------------------- persistence
def load(run_id: str) -> dict[str, Any]:
    with db.session() as s:
        r = s.get(AgentRunRow, run_id)
        if r is None:
            raise RunError(404, "run not found")
        return {"id": r.id, "pattern_id": r.pattern_id, "user_id": r.user_id, "status": r.status,
                "cost_so_far_usd": r.cost_so_far_usd, "lease_owner": r.lease_owner, "lease_until": r.lease_until,
                "created_at": r.created_at, "updated_at": r.updated_at, **r.data}


def save(run: dict[str, Any], status: str | None = None, owner: str | None = None, claim: str | None = None) -> None:
    keys = ("id", "pattern_id", "user_id", "status", "cost_so_far_usd", "lease_owner", "lease_until", "created_at",
            "updated_at")
    data = {k: v for k, v in run.items() if k not in keys}
    run["status"] = status or run["status"]
    with db.session() as s:
        r = s.get(AgentRunRow, run["id"])
        assert r is not None
        if owner is not None and r.lease_owner not in (None, owner):
            raise RunError(409, "lease lost")       # another worker took over: never write over its state
        r.data = json.loads(json.dumps(data, default=str))
        r.status = run["status"]
        r.cost_so_far_usd = float(run.get("cost_so_far_usd", 0.0))
        r.updated_at = time.time()
        if owner is not None:
            r.lease_until = time.time() + LEASE_S
        if claim is not None:                       # take the lease in the same write that makes it runnable
            r.lease_owner, r.lease_until = claim, time.time() + LEASE_S


def event(run: dict[str, Any], kind: str, text: str, **extra: Any) -> None:
    run.setdefault("events", []).append({"n": len(run["events"]) + 1, "ts": time.time(), "kind": kind, "text": text,
                                         **extra})


def _set_pattern_status(pattern_id: str, status: PatternStatus, run: dict[str, Any] | None = None) -> None:
    if run is not None and run.get("intake", {}).get("source_code"):
        return                      # training edited code never changes the pattern's lifecycle until Accept installs it
    p = store().get_pattern(pattern_id)
    if p is not None and p.status != PatternStatus.approved:     # an approved artifact keeps serving
        p.status = status
        store().put_pattern(p)


# ----------------------------------------------------------------------------- intake
def intake_summary(pattern_id: str) -> dict[str, Any]:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise RunError(404, "pattern not found")
    from .tools import Toolbox as _T

    labelled = len(_T("preview", p, {"intake": {}, "budget": {"max_cost_usd": 0}}, "system")._logged())
    task = p.task_type.value if p.task_type else "unknown"
    if task == "scoring":
        plan = (f"Fine-tune a small transformer (dl) on {labelled:,} LLM-labelled examples through the training-job "
                "interface; fall back to a rule if it can't beat it.")
    elif task in ("classification", "routing"):
        enough = "enough for ML" if labelled >= settings.ml_min_samples else f"below the {settings.ml_min_samples} ML needs"
        plan = f"Try rules first; {labelled:,} labelled examples are available, {enough}."
    elif task == "lookup":
        plan = "Extract the fact table from the attached documents and compile a lookup; it declares its documents."
    else:
        plan = "Write a deterministic rule from the examples and verify it against the LLM."
    monthly = p.projected_monthly_savings_usd
    budget = {"max_iterations": settings.agent_max_iterations, "max_cost_usd": settings.agent_max_cost_usd}
    first_iter = round(cost(settings.heavy_model_id, 2000, 300) * 6 + 0.002, 4)
    return {"pattern": p.model_dump(mode="json", exclude={"centroid", "exemplars", "handler_code"}),
            "examples": p.examples[:8], "labelled_examples": labelled, "suggested_plan": plan, "budget": budget,
            "first_iteration_estimate_usd": first_iter,
            "payback_days": round(budget["max_cost_usd"] / (monthly / 30), 1) if monthly > 0 else None,
            "projected_monthly_savings_usd": monthly, "frequency_per_day": p.frequency_per_day,
            "projected_tokens_per_day": p.projected_tokens_per_day}


def create_run(pattern_id: str, user_id: str, intake: dict[str, Any], start: bool = False) -> dict[str, Any]:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise RunError(404, "pattern not found")
    budget = {"max_iterations": int(intake.get("max_iterations") or settings.agent_max_iterations),
              "max_cost_usd": float(intake.get("max_cost_usd") or settings.agent_max_cost_usd)}
    for iid in intake.get("integration_ids", []):
        integrations.get(iid)                                       # must exist
    run_id = new_id("run")
    state = {"intake": {k: intake.get(k) or [] if k.endswith("_ids") else intake.get(k) or ""
                        for k in ("instructions", "dataset_ids", "integration_ids", "collection_ids", "document_ids",
                                  "source_code", "source_kind")},
             "budget": budget, "iteration": 0, "step": 0, "iterations": [], "events": [], "candidates": {},
             "feedback": [], "task_type": p.task_type.value if p.task_type else None, "cost_so_far_usd": 0.0}
    with db.session() as s:
        s.add(AgentRunRow(id=run_id, pattern_id=pattern_id, user_id=user_id, status="awaiting_intake", data=state,
                          cost_so_far_usd=0.0, created_at=time.time(), updated_at=time.time()))
    _set_pattern_status(pattern_id, PatternStatus.awaiting_intake, {"intake": state["intake"]})
    run = load(run_id)
    event(run, "intake", "Intake answers saved. Nothing runs until Start.")
    save(run)
    if start:
        return start_run(run_id)
    return load(run_id)


def start_run(run_id: str, sync: bool = False) -> dict[str, Any]:
    run = load(run_id)
    if run["status"] not in ("awaiting_intake", "awaiting_feedback"):
        raise RunError(409, f"run is {run['status']}")
    run.pop("integration_spec", None)
    for iid in run["intake"].get("integration_ids", []):
        integ = integrations.get(iid)
        if integ["status"] != "approved":
            raise RunError(409, f"integration {integ['name']!r} still needs an admin's approval (rule 11)")
        if integ["type"] == "http" and integ["role"] == "feature_source":
            cfg = integ["config"]
            run["integration_spec"] = {"url": integ["target"], "param": cfg.get("param", "email"),
                                       "param_from": cfg.get("param_from", "email"), "field": cfg.get("field", "value"),
                                       "map": cfg.get("map", {}), "default": cfg.get("default", ""),
                                       "format": cfg.get("format", "{prefix}{label}"), "integration_id": iid}
    # Start begins iteration 1; every later start (improve / add input) is the next iteration
    run["iteration"] = 1 if run["status"] == "awaiting_intake" else int(run["iteration"]) + 1
    run["step"] = 0
    event(run, "start", f"Iteration {run['iteration']} started.")
    # A synchronous start (the seed, the demo's lookup rebuild) owns the run from the moment it becomes runnable,
    # so an agent-worker polling for runs can never take it over in between.
    owner = f"{WORKER_ID}-sync-{threading.get_ident()}" if sync else None
    save(run, "agent_running", claim=owner)
    _set_pattern_status(run["pattern_id"], PatternStatus.agent_running, run)
    dispatch(run_id, sync, owner)
    return load(run_id)


def dispatch(run_id: str, sync: bool = False, owner: str | None = None) -> None:
    if sync:
        execute(run_id, owner=owner)
    elif settings.agent_executor == "inline":
        _pool.submit(_safe_execute, run_id)
    # executor == "worker": the agent-worker process picks it up (app/agent/worker.py)


def _safe_execute(run_id: str) -> None:
    try:
        execute(run_id)
    except Exception:  # noqa: BLE001 - recorded on the run
        log.exception("agent run %s crashed", run_id)


# ----------------------------------------------------------------------------- execution
def acquire(run_id: str, owner: str) -> bool:
    now = time.time()
    with db.session() as s:
        res = s.execute(update(AgentRunRow).where(
            AgentRunRow.id == run_id, AgentRunRow.status == "agent_running",
            or_(AgentRunRow.lease_owner.is_(None), AgentRunRow.lease_owner == owner, AgentRunRow.lease_until < now))
            .values(lease_owner=owner, lease_until=now + LEASE_S))
        return bool(getattr(res, "rowcount", 0))


def release(run_id: str, owner: str) -> None:
    with db.session() as s:
        s.execute(update(AgentRunRow).where(AgentRunRow.id == run_id, AgentRunRow.lease_owner == owner)
                  .values(lease_owner=None, lease_until=None))


def execute(run_id: str, owner: str | None = None, max_steps: int = 60) -> dict[str, Any]:
    """Run steps until the agent reports, asks, hits a cap, or the run is stopped. Resumable."""
    owner = owner or f"{WORKER_ID}-{threading.get_ident()}"
    if not acquire(run_id, owner):
        return load(run_id)
    stop = threading.Event()

    def heartbeat() -> None:                    # long steps (dl training) keep the lease alive
        while not stop.wait(LEASE_S / 3):
            try:
                with db.session() as s:
                    s.execute(update(AgentRunRow).where(AgentRunRow.id == run_id, AgentRunRow.lease_owner == owner)
                              .values(lease_until=time.time() + LEASE_S))
            except Exception:  # noqa: BLE001 - a dropped connection: retry on the next beat, keep the thread alive
                log.warning("lease heartbeat for %s failed; retrying", run_id, exc_info=True)

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        for _ in range(max_steps):
            run = load(run_id)
            if run["status"] != "agent_running":
                break
            if not _step(run, owner):
                break
        return load(run_id)
    finally:
        stop.set()
        release(run_id, owner)


def _brain_action(run: dict[str, Any], tb: Toolbox) -> dict[str, Any]:
    """Ask the agent LLM for the next tool call; its tokens are agent overhead."""
    view = {k: run.get(k) for k in ("task_type", "iteration", "step", "intake", "feedback", "last_candidate")}
    if run["intake"].get("source_code"):            # the user's edited code: a fixed plan, no agent decisions
        return scripted.next_action(run)
    if settings.llm == "bedrock" and settings.agent_llm == "bedrock":
        from .bedrock_agent import next_action

        return next_action(run, tb)
    if settings.llm == "bedrock":                   # scripted agent: only the Demo LLM can "play" the script
        return scripted.next_action(view)
    res = heavy_model().complete(LLMRequest(question=f"Next tool call for run {run['id']}", system=AGENT_SYSTEM,
                                            purpose="agent", meta={"state": view}))
    usd = cost(res.model_id, res.input_tokens, res.output_tokens)
    tb.charge(usd, "agent", res.input_tokens, res.output_tokens, res.model_id, note=f"step {run['step']}")
    return dict(json.loads(res.text))


def _step(run: dict[str, Any], owner: str) -> bool:
    p = store().get_pattern(run["pattern_id"])
    assert p is not None
    tb = Toolbox(run["id"], p, run, run["user_id"])
    try:
        tb.check_budget()
        if run["iteration"] > run["budget"]["max_iterations"]:
            raise BudgetExceeded(f"iteration cap {run['budget']['max_iterations']} reached")
        t0 = time.monotonic()
        action = _brain_action(run, tb)
        tool, args = action["tool"], dict(action.get("args", {}))
        log.info("run %s step %s: agent chose %s in %.1fs", run["id"], run.get("step"), tool, time.monotonic() - t0)
        run["pending_tool_use"] = action.get("tool_use_id")     # Bedrock: answered with a toolResult next turn
        if tool == "report":
            text = scripted.report_text(run, args.get("recommendation", "accept"))
            cand = run["candidates"].get(run.get("last_candidate") or "", {})
            run["iterations"].append({"n": run["iteration"], "plan": args.get("recommendation"),
                                      "candidate": {k: cand.get(k) for k in ("id", "kind", "uri", "params")},
                                      "metrics": cand.get("metrics"), "confusion": cand.get("confusion"),
                                      "errors": (cand.get("errors") or [])[:8], "report_md": text,
                                      "user_response": None})
            event(run, "report", f"Iteration {run['iteration']} report ready.", report=text)
            save(run, "awaiting_feedback", owner)
            _set_pattern_status(run["pattern_id"], PatternStatus.awaiting_feedback, run)
            _notify(run, f"Agent is waiting for your feedback: {p.name}")
            return False
        if tool == "ask_user":
            run["pending_question"] = args.get("question", "")
            event(run, "question", run["pending_question"])
            save(run, "awaiting_feedback", owner)
            return False
        t1 = time.monotonic()
        result = _call_tool(tb, tool, args, run)
        log.info("run %s step %s: %s took %.1fs", run["id"], run.get("step"), tool, time.monotonic() - t1)
        run["step"] = int(run.get("step", 0)) + 1
        event(run, "tool", f"{tool} → {summarize(result)}", tool=tool, args=args, result=result,
              tool_use_id=run.get("pending_tool_use"))
        save(run, None, owner)                       # checkpoint after every step
        return True
    except BudgetExceeded as exc:
        run["cap_reached"] = str(exc)
        event(run, "budget", f"Paused: {exc}. Raise the budget or stop.")
        save(run, "awaiting_feedback", owner)
        _set_pattern_status(run["pattern_id"], PatternStatus.awaiting_feedback, run)
        _notify(run, f"Agent paused at its budget cap: {p.name}")
        return False
    except ToolError as exc:
        event(run, "error", f"Tool error: {exc}", tool_use_id=run.get("pending_tool_use"))
        run["step"] = int(run.get("step", 0)) + 1
        save(run, None, owner)
        return True


def _call_tool(tb: Toolbox, tool: str, args: dict[str, Any], run: dict[str, Any]) -> Any:
    if tool == "get_pattern_summary":
        return tb.get_pattern_summary()
    if tool == "build_dataset":
        return tb.build_dataset()
    if tool == "label_with_llm":
        return tb.label_with_llm(int(args.get("n", 50)))
    if tool == "write_rule":
        code = args.get("code", "")
        if code == "$keyword_rule":
            code = scripted.keyword_rule(run)
        elif code == "$compiled_rule":
            from ..demo_rules import compile_rule

            code = compile_rule({"examples": tb.pattern.examples, "task_type": run.get("task_type")})
        out = tb.write_rule(code)
        run["last_candidate"] = out["candidate_id"]
        return out
    if tool == "write_lookup_rule":
        from ..demo_rules import lookup_rule

        out = tb.write_rule(lookup_rule(run["lookup"]["table"]))
        run["last_candidate"] = out["candidate_id"]
        holdout = [{"input": f"How many vacation days do employees in {k} get?", "label": v, "source": "doc"}
                   for k, v in sorted(run["lookup"]["table"].items())]
        run["dataset"] = {"train": [], "val": [], "holdout": holdout, "user_eval": []}
        return out
    if tool == "train_ml":
        out = tb.train_ml(args.get("features", "tfidf"), args.get("model", "logreg"), args.get("params"))
        run["last_candidate"] = out["candidate_id"]
        return out
    if tool == "train_dl":
        out = tb.train_dl(args.get("base_model"), args.get("task", "sequence_classification"), args.get("params"))
        run["last_candidate"] = out["candidate_id"]
        return out
    if tool == "train_source":
        if not run["intake"].get("source_code"):
            raise ToolError("train_source runs only the user's edited code")
        out = tb.train_source(run["intake"]["source_code"], run["intake"].get("source_kind") or "ml")
        run["last_candidate"] = out["candidate_id"]
        return out
    if tool == "evaluate":
        return tb.evaluate(args["candidate_id"])
    if tool == "inspect_errors":
        return tb.inspect_errors(args["candidate_id"], int(args.get("k", 8)))
    if tool == "call_integration":
        return tb.call_integration(args["integration_id"], args.get("payload", {}))
    if tool == "search_knowledge":
        return tb.search_knowledge(args["query"], args.get("collection_ids", []))
    if tool == "extract_table":
        return tb.extract_table(args.get("document_ids") or run["intake"].get("document_ids", []), args["schema"])
    raise ToolError(f"unknown or forbidden tool {tool!r}")     # approve / deploy / config are not tools


def summarize(result: Any) -> str:
    if isinstance(result, dict):
        keys = ("agreement", "candidate_id", "train", "rows", "seconds", "labelled")
        parts = [f"{k}={result[k]}" for k in keys if k in result]
        return ", ".join(parts) or ", ".join(list(result)[:4])
    if isinstance(result, list):
        return f"{len(result)} items"
    return str(result)[:80]


def _notify(run: dict[str, Any], title: str) -> None:
    with db.session() as s:
        s.add(Notification(user_id=run["user_id"], kind="run_waiting", title=title,
                           link=f"/agents/run/?id={run['id']}", ref_id=run["id"], created_at=time.time()))


# ----------------------------------------------------------------------------- user responses
def respond(run_id: str, action: str, feedback: str = "", user_id: str = "", sync: bool = False,
            intake: dict[str, Any] | None = None) -> dict[str, Any]:
    run = load(run_id)
    if run["status"] != "awaiting_feedback":
        raise RunError(409, f"run is {run['status']}, not waiting for feedback")
    if run["iterations"]:
        run["iterations"][-1]["user_response"] = {"action": action, "feedback": feedback, "by": user_id}
    if action == "stop":
        event(run, "stop", "Stopped by the user. The pattern stays on the LLM.")
        save(run, "stopped")
        _set_pattern_status(run["pattern_id"], PatternStatus.recognized, run)
        return load(run_id)
    if action == "improve":
        run["feedback"].append(feedback)
        event(run, "feedback", f"Keep improving: {feedback}")
        save(run)
        return start_run(run_id, sync)
    if action == "add_input":
        for k, v in (intake or {}).items():
            if k.endswith("_ids"):
                run["intake"][k] = sorted(set(run["intake"].get(k, [])) | set(v))
            elif v:
                run["intake"][k] = v
        event(run, "intake", "Intake updated with new data / integrations.")
        save(run)
        return start_run(run_id, sync)
    if action == "accept":
        try:
            return _accept(run, user_id)
        except (RunError, ToolError) as exc:
            run = load(run_id)
            event(run, "gate", f"Verification could not run: {exc}")
            save(run, "awaiting_feedback")
            return load(run_id)
    raise RunError(422, "action must be accept, improve, add_input or stop")


def _accept(run: dict[str, Any], user_id: str) -> dict[str, Any]:
    """The user's Accept still goes through the verification gate (rule 10); approval stays human."""
    save(run, "verifying")
    cand_id = run.get("last_candidate")
    cand = run["candidates"].get(cand_id or "")
    if not cand:
        raise RunError(409, "no candidate to accept")
    p = store().get_pattern(run["pattern_id"])
    assert p is not None
    tb = Toolbox(run["id"], p, run, run["user_id"])
    metrics = tb.evaluate(cand_id or "")           # the gate: fresh holdout evaluation, user labels take precedence
    threshold = 1.0 if p.task_type == TaskType.lookup else settings.verify_min_score
    passed = (metrics["agreement"] >= threshold and metrics["coverage"] >= 0.5
              and (metrics["user_label_accuracy"] is None or metrics["user_label_accuracy"] >= threshold - 0.05))
    if not passed:
        event(run, "gate", f"Verification gate failed: agreement {metrics['agreement']:.1%} (needs {threshold:.0%}), "
                           f"coverage {metrics['coverage']:.0%} (needs 50%). Keep improving or add data.")
        save(run, "awaiting_feedback")
        return load(run["id"])
    _install_candidate(run, p, cand, metrics, threshold, user_id)
    event(run, "gate", f"Verification passed ({metrics['agreement']:.1%}). Waiting for an admin to approve and deploy.")
    save(run, "accepted")
    with db.session() as s:
        s.add(Notification(user_id=None, kind="approval_needed", title=f"{p.name} passed verification — approve to deploy",
                           link=f"/patterns/?id={p.id}", ref_id=p.id, created_at=time.time()))
    return load(run["id"])


def _install_candidate(run: dict[str, Any], p: Pattern, cand: dict[str, Any], metrics: dict[str, Any],
                       threshold: float, user_id: str) -> None:
    from ..light.recognizer import refresh_recognizer
    from ..runtime import runtime

    was_approved = p.status == PatternStatus.approved
    p.version += 1
    p.kind = ArtifactKind(cand["kind"])
    final = Path(settings.data_dir) / "models" / p.id / f"v{p.version}"
    if final.exists():
        shutil.rmtree(final)
    shutil.copytree(ensure_local(Path(cand["uri"])), final)
    publish(final)
    p.model_uri = str(final)
    is_model = cand["kind"] in ("ml", "dl")
    p.handler_code = "" if is_model else cand.get("code") or ""
    if run.get("lookup"):
        from ..schemas import KbDependency

        p.kb_dependencies = [KbDependency.model_validate(d) for d in run["lookup"]["deps"]]
        p.examples = [r["input"] for r in run["dataset"]["holdout"]]
        p.model_card["lookup"] = {"schema": run["lookup"]["schema"], "rows": len(run["lookup"]["table"]),
                                  "slots": {r["input"]: k for r, k in zip(run["dataset"]["holdout"],
                                                                           sorted(run["lookup"]["table"]), strict=True)}}
    meta = {}
    if (final / "meta.json").exists():
        meta = json.loads((final / "meta.json").read_text())
    p.change_note = f"agent run {run['id']}, iteration {run['iteration']} ({cand['kind']})"
    if is_model and (cand.get("params") or {}).get("source"):
        p.source_code = cand.get("code") or ""                      # the user's edited code, served as written
        retrain = str(run["intake"].get("instructions", "")).startswith("Retrain")
        p.change_note = (f"retrained with more data (run {run['id']})" if retrain
                         else f"trained from edited code (run {run['id']})")
    else:
        p.source_code = artifact_source(cand["kind"], meta, cand.get("params"), p.name, p.version,
                                        len(run.get("dataset", {}).get("train", [])))
    p.model_card.update({"kind": cand["kind"], "params": cand.get("params"), "metrics": metrics,
                         "training_size": len(run.get("dataset", {}).get("train", [])),
                         "features": meta.get("features"), "classes": meta.get("classes"),
                         "agent_run": run["id"], "iteration": run["iteration"], "confusion": cand.get("confusion")})
    if run.get("integration_spec"):
        p.not_fully_deterministic = True
        p.integration_ids = list(run["intake"].get("integration_ids", []))
    p.verification = VerifyResult(pattern_id=p.id, score=metrics["agreement"], sample_size=metrics["holdout"],
                                  method="holdout_vs_llm" + ("+user_labels" if metrics["user_label_accuracy"] is not None
                                                             else ""),
                                  passed=True)
    p.verification_score = metrics["agreement"]
    p.status = PatternStatus.draft
    p.alert = None
    if was_approved:
        from ..deploy import teardown

        teardown(p.id)
    store().put_pattern(p)
    runtime().stop(p.id)
    refresh_recognizer()


def raise_budget(run_id: str, max_cost_usd: float | None = None, max_iterations: int | None = None,
                 sync: bool = False) -> dict[str, Any]:
    run = load(run_id)
    if max_cost_usd is not None:
        run["budget"]["max_cost_usd"] = float(max_cost_usd)
    if max_iterations is not None:
        run["budget"]["max_iterations"] = int(max_iterations)
    capped = run.pop("cap_reached", None)
    event(run, "budget", f"Budget raised to ${run['budget']['max_cost_usd']:.2f} / "
                         f"{run['budget']['max_iterations']} iterations.")
    save(run)
    if capped and run["status"] == "awaiting_feedback":
        owner = f"{WORKER_ID}-sync-{threading.get_ident()}" if sync else None
        save(run, "agent_running", claim=owner)
        dispatch(run_id, sync, owner)
    return load(run_id)


def runnable() -> list[str]:
    now = time.time()
    with db.session() as s:
        return list(s.scalars(select(AgentRunRow.id).where(
            AgentRunRow.status == "agent_running",
            or_(AgentRunRow.lease_owner.is_(None), AgentRunRow.lease_until < now))))
