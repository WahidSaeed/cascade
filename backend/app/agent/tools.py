"""Agent tools (BUILD.md §11.3): typed functions exposed to the model. The TOOLS enforce the rules, not
the prompt: the agent cannot approve, deploy, change config, call unregistered/unapproved endpoints, or
exceed its budget. Every LLM call, label and training job is ledger overhead tagged with the run.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .. import db
from ..compilers import dl as dlc
from ..compilers import ml as mlc
from ..config import settings
from ..heavy import LLMRequest, heavy_model
from ..ledger import OverheadEntry, ledger
from ..models import RequestLog
from ..patterns import leak_warnings
from ..pricing import cost, lambda_cost
from ..runtime import SubprocessRuntime
from ..schemas import ArtifactKind, Pattern
from ..store import store
from ..verifier import comparator, record_llm_overhead
from . import datasets, integrations

FALLBACK = "__FALLBACK__"


class BudgetExceeded(RuntimeError):
    pass


class ToolError(ValueError):
    pass


class Toolbox:
    """Tools bound to one run. `state` is the run's persisted JSON (mutated in place, saved by the runner)."""

    def __init__(self, run_id: str, pattern: Pattern, state: dict[str, Any], user_id: str) -> None:
        self.run_id = run_id
        self.pattern = pattern
        self.state = state
        self.user_id = user_id

    # ------------------------------------------------------------------ budget & overhead
    def spent(self) -> float:
        """What this run has cost so far: the ledger's overhead rows for the run (the single source of truth,
        so a step re-run after a crash can never make the run's figure drift from the ledger)."""
        from sqlalchemy import func

        from ..models import OverheadRow

        with db.session() as s:
            total = s.scalar(select(func.coalesce(func.sum(OverheadRow.cost_usd), 0.0))
                             .where(OverheadRow.run_id == self.run_id))
        self.state["cost_so_far_usd"] = round(float(total or 0.0), 8)
        return float(self.state["cost_so_far_usd"])

    def charge(self, usd: float, category: str, tokens_in: int = 0, tokens_out: int = 0, model_id: str | None = None,
               note: str = "") -> None:
        ledger().record_overhead(OverheadEntry(category=category, input_tokens=tokens_in, output_tokens=tokens_out,
                                               cost_usd=usd, model_id=model_id, pattern_id=self.pattern.id,
                                               run_id=self.run_id, user_id=self.user_id, note=note))
        self.spent()

    def check_budget(self, upcoming_usd: float = 0.0) -> None:
        cap = float(self.state["budget"]["max_cost_usd"])
        if self.spent() + upcoming_usd > cap:
            raise BudgetExceeded(f"budget cap ${cap:.2f} reached (spent ${self.spent():.4f})")

    # ------------------------------------------------------------------ data
    def get_pattern_summary(self) -> dict[str, Any]:
        p = self.pattern
        return {"name": p.name, "task_type": p.task_type, "examples": p.examples[:8], "frequency_per_day":
                p.frequency_per_day, "avg_input_tokens": p.avg_input_tokens, "avg_output_tokens": p.avg_output_tokens,
                "projected_tokens_per_day": p.projected_tokens_per_day}

    def _logged(self) -> list[tuple[str, str]]:
        """Logged prompts of this pattern with the LLM's answers (distillation labels), PII-redacted inputs."""
        p = self.pattern
        keys = set(p.template_keys)
        with db.session() as s:
            rows = s.execute(select(RequestLog.redacted, RequestLog.answer, RequestLog.template_key)
                             .where(RequestLog.route == "llm").order_by(RequestLog.ts)).all()
        out = [(r, a) for r, a, k in rows if k in keys and a]
        return list(dict.fromkeys(out))

    def build_dataset(self, sources: list[str] | None = None, split: tuple[float, float, float] = (0.7, 0.1, 0.2)
                      ) -> dict[str, Any]:
        """Merge LLM-labelled logs and the user's datasets into train / val / holdout (user labels win)."""
        rng = random.Random(f"{self.pattern.id}-dataset")          # reproducible across runs (§14.4)
        logged = [{"input": i, "label": lbl, "source": "llm"} for i, lbl in self._logged()]
        rng.shuffle(logged)
        n = len(logged)
        n_train, n_val = int(n * split[0]), int(n * split[1])
        train, val, holdout = logged[:n_train], logged[n_train:n_train + n_val], logged[n_train + n_val:]
        user_eval: list[dict[str, str]] = []
        prompt_tmpl = self.pattern.examples[0] if self.pattern.examples else "'{t}'"
        for ds_id in self.state["intake"].get("dataset_ids", []):
            meta = datasets.get(ds_id)
            rows = [{"input": wrap_like(prompt_tmpl, r["input"]), "label": r.get("label", ""), "source": "user"}
                    for r in datasets.load(ds_id) if r.get("label")]
            rng.shuffle(rows)
            if meta["use"] == "train":
                train += rows
            elif meta["use"] == "eval":
                user_eval += rows
            else:                               # both: a quarter is held out for evaluation
                k = max(1, len(rows) // 4)
                user_eval += rows[:k]
                train += rows[k:]
        # user labels take precedence over the LLM's for the same input (matched on the payload, whatever the
        # instruction phrasing around it)
        def key(prompt: str) -> str:
            return " ".join((mlc.payload_text(prompt, "quoted") or prompt).lower().split())

        user_labels = {key(r["input"]): r["label"] for r in train + user_eval if r["source"] == "user"}
        for r in train + val + holdout:
            payload = key(r["input"])
            if payload in user_labels:
                r["label"], r["source"] = user_labels[payload], "user"
        # a prompt the user set aside for evaluation must not also be trained on (or it measures nothing)
        eval_keys = {key(r["input"]) for r in user_eval}
        train = [r for r in train if key(r["input"]) not in eval_keys]
        seen: set[str] = set()
        train = [r for r in train if not (key(r["input"]) in seen or seen.add(key(r["input"])))]  # type: ignore[func-returns-value]
        self.state["dataset"] = {"train": train, "val": val, "holdout": holdout, "user_eval": user_eval}
        dist = Counter(r["label"] for r in train)
        return {"train": len(train), "val": len(val), "holdout": len(holdout), "user_eval": len(user_eval),
                "labels": dict(dist.most_common()), "llm_labelled": n, "user_labelled": sum(
                    1 for r in train if r["source"] == "user")}

    def label_with_llm(self, n: int) -> dict[str, Any]:
        """Extra labels from the heavy LLM for unlabelled logged prompts (overhead, budget-checked)."""
        p = self.pattern
        keys = set(p.template_keys)
        with db.session() as s:
            prompts = [r for (r, k) in s.execute(select(RequestLog.redacted, RequestLog.template_key)
                                                 .where(RequestLog.route != "llm")) if k in keys][:n]
        added = 0
        for q in prompts:
            self.check_budget(0.001)
            res = heavy_model().complete(LLMRequest(question=q, purpose="label"))
            record_llm_overhead(res, "label", p.id, self.run_id)
            self.spent()
            self.state.setdefault("dataset", {}).setdefault("train", []).append(
                {"input": q, "label": res.text, "source": "llm"})
            added += 1
        return {"labelled": added}

    # ------------------------------------------------------------------ candidates
    def _cand_dir(self, cid: str) -> Path:
        return Path(settings.data_dir) / "agent" / self.run_id / cid

    def _register(self, kind: str, params: dict[str, Any], path: Path, code: str = "") -> str:
        cid = f"c{len(self.state.setdefault('candidates', {})) + 1}"
        self.state["candidates"][cid] = {"id": cid, "kind": kind, "uri": str(path), "params": params, "code": code,
                                         "metrics": None, "created_at": time.time()}
        return cid

    def write_rule(self, code: str) -> dict[str, Any]:
        cid = f"c{len(self.state.setdefault('candidates', {})) + 1}"
        d = self._cand_dir(cid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "handler.py").write_text(code)
        (d / "manifest.json").write_text(json.dumps({"kind": "rule"}))
        self._register("rule", {}, d, code)
        warnings = leak_warnings(code, [r["input"] for r in self.state.get("dataset", {}).get("train", [])])
        self.state["candidates"][cid]["leak_warnings"] = warnings
        return {"candidate_id": cid, "leak_warnings": warnings}

    def train_ml(self, features: str = "tfidf", model: str = "logreg", params: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
        ds = self.state.get("dataset")
        if not ds or not ds["train"]:
            raise ToolError("build_dataset first")
        if len(ds["train"]) < settings.ml_min_samples:
            raise ToolError(f"ml needs at least {settings.ml_min_samples} labelled samples (have {len(ds['train'])})")
        self.check_budget(0.0005)
        prm = mlc.MlParams(model=model, **{k: v for k, v in (params or {}).items() if k in mlc.MlParams.__annotations__})
        cid = f"c{len(self.state.setdefault('candidates', {})) + 1}"
        prefixes = prompt_prefixes([r["input"] for r in ds["train"]])
        spec = self.state.get("integration_spec")
        # with a feature-source integration the model learns the category; the integration adds the rest
        labels = [strip_prefix(r["label"], spec) for r in ds["train"]]
        trained = mlc.train_ml([r["input"] for r in ds["train"]], labels, self._cand_dir(cid), prm, prefixes=prefixes)
        if spec:
            meta = json.loads((trained.path / "meta.json").read_text())
            meta["integration"] = {k: v for k, v in spec.items() if k != "integration_id"}
            (trained.path / "meta.json").write_text(json.dumps(meta))
        self._register("ml", {"features": features, **asdict(prm)}, trained.path)
        self.charge(dlc.training_cost(trained.seconds), "training", note=f"train_ml {cid}")
        return {"candidate_id": cid, "train_size": trained.train_size, "classes": trained.classes,
                "seconds": trained.seconds, "size_mb": trained.size_mb}

    def train_dl(self, base_model: str | None = None, task: str = "sequence_classification",
                 params: dict[str, Any] | None = None) -> dict[str, Any]:
        ds = self.state.get("dataset")
        if not ds or not ds["train"]:
            raise ToolError("build_dataset first")
        need = settings.dl_min_samples
        if len(ds["train"]) < need:
            raise ToolError(f"dl needs at least {need} labelled samples (have {len(ds['train'])})")
        self.check_budget(0.01)
        cid = f"c{len(self.state.setdefault('candidates', {})) + 1}"
        out = self._cand_dir(cid)
        data = out.with_suffix(".jsonl")
        dlc.write_dataset([], [r["label"] for r in ds["train"]], [mlc.payload_text(r["input"], "quoted") or r["input"]
                                                                  for r in ds["train"]], data)
        prefixes = "|".join(prompt_prefixes([r["input"] for r in ds["train"]]))
        t = dlc.trainer()
        job = t.start(data, out, {"epochs": 8, "lr": 5e-4, **(params or {}), "prefixes": prefixes})
        status = dlc.wait(t, job)
        if status == "Completed":
            t.fetch(job)
        seconds = time.time() - job.started
        self.charge(dlc.training_cost(seconds), "training", note=f"train_dl {cid} ({job.trainer})")
        if status != "Completed":
            raise ToolError(f"training job {job.job_id} {status}")
        meta = json.loads((out / "meta.json").read_text())
        self._register("dl", {"base_model": base_model or settings.dl_base_model, "task": task, "job": job.job_id,
                              **(params or {})}, out)
        return {"candidate_id": cid, "job_id": job.job_id, "seconds": round(seconds, 1), "classes": meta["classes"],
                "final_loss": meta.get("final_loss")}

    def adopt_candidate(self, kind: str, path: str, params: dict[str, Any]) -> str:
        """Register an artifact trained elsewhere (e.g. a cached seed model) as a candidate."""
        return self._register(kind, params, Path(path))

    # ------------------------------------------------------------------ integrations & knowledge
    def call_integration(self, integration_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if integration_id not in self.state["intake"].get("integration_ids", []):
            raise ToolError("only integrations registered at this run's intake can be called")
        return integrations.call(integration_id, payload)

    def search_knowledge(self, query: str, collection_ids: list[str]) -> list[dict[str, Any]]:
        from ..rag.service import search

        allowed = set(self.state["intake"].get("collection_ids", []))
        cols = [c for c in collection_ids if c in allowed]
        context: list[dict[str, Any]] = search(query, self.user_id, cols)["context"]   # access-filtered as the run's user
        return context

    def extract_table(self, document_ids: list[str], schema: str) -> dict[str, Any]:
        from ..lookup import extract_table
        from ..rag.access import can_read_documents

        if not can_read_documents(document_ids, store().principals(self.user_id)):
            raise ToolError("the run's user cannot read all of these documents")
        self.check_budget(0.002)
        table, deps = extract_table(document_ids, schema, self.pattern.id, self.run_id)
        self.spent()
        self.state["lookup"] = {"table": table, "deps": [d.model_dump() for d in deps], "schema": schema}
        return {"rows": len(table), "dependencies": [f"{d.title} v{d.version}" for d in deps]}

    # ------------------------------------------------------------------ evaluation
    def _invoke_all(self, cand: dict[str, Any], prompts: list[str]) -> list[tuple[str | None, float, float, str | None]]:
        """Run the candidate OUT OF PROCESS (the same runtime that serves artifacts). With an integration, the
        offline evaluation scores the model's own part (logged prompts are PII-redacted, so the external
        lookup can't run on them); the live integration call is exercised separately."""
        uri = cand["uri"]
        if self.state.get("integration_spec") and (Path(uri) / "meta.json").exists():
            eval_dir = Path(uri + "-eval")
            if eval_dir.exists():
                shutil.rmtree(eval_dir)
            shutil.copytree(uri, eval_dir)
            meta = json.loads((eval_dir / "meta.json").read_text())
            meta.pop("integration", None)
            (eval_dir / "meta.json").write_text(json.dumps(meta))
            uri = str(eval_dir)
        probe = Pattern(id=f"cand-{self.run_id}-{cand['id']}-{int(time.time() * 1000) % 100000}", name="candidate",
                        kind=ArtifactKind(cand["kind"]), handler_code=cand.get("code", ""), model_uri=uri)
        rt = SubprocessRuntime()
        try:
            out = []
            for q in prompts:
                r = rt.invoke(probe, q)
                out.append((r.result, r.confidence, r.latency_ms, r.error))
            return out
        finally:
            rt.shutdown()

    def evaluate(self, candidate_id: str) -> dict[str, Any]:
        cand = self.state.get("candidates", {}).get(candidate_id or "")
        if cand is None:
            raise ToolError("unknown candidate (the previous step produced none)")
        ds = self.state["dataset"]
        holdout = ds["holdout"] or ds["val"] or ds.get("user_eval", [])   # no logs yet: the user's held-out rows
        _, base_agree = comparator(self.pattern.task_type.value if self.pattern.task_type else None)
        spec = self.state.get("integration_spec")

        def agree(out: str, label: str) -> float:
            return base_agree(strip_prefix(out, spec), strip_prefix(label, spec))

        res = self._invoke_all(cand, [r["input"] for r in holdout])
        per_class: dict[str, list[float]] = {}
        scores, lat, errors = [], [], []
        answered = 0
        for r, (out, conf, ms, err) in zip(holdout, res, strict=True):
            lat.append(ms)
            deferred = bool(err) or out is None or out == FALLBACK or conf < settings.artifact_min_confidence
            if deferred:
                # a deferral is not a wrong answer: the LLM answers it, so quality holds; it costs coverage
                low = not err and out not in (None, FALLBACK)
                errors.append({"prompt": r["input"], "expected": r["label"], "got": out if not err else f"ERROR {err}",
                               "confidence": round(conf, 3), "kind": "low_confidence" if low else "deferred"})
                continue
            answered += 1
            a = agree(out or "", r["label"])
            scores.append(a)
            per_class.setdefault(r["label"], []).append(a)
            if a < 1.0:
                errors.append({"prompt": r["input"], "expected": r["label"], "got": out, "confidence": round(conf, 3),
                               "kind": "wrong"})
        user = ds.get("user_eval", [])
        user_acc = None
        if user and user is not holdout:
            ures = self._invoke_all(cand, [r["input"] for r in user])
            judged = [(r, o) for r, (o, c, _, e) in zip(user, ures, strict=True)
                      if not e and o not in (None, FALLBACK) and c >= settings.artifact_min_confidence]
            user_acc = round(sum(agree(o or "", r["label"]) for r, o in judged) / len(judged), 4) if judged else 0.0
        size = sum(f.stat().st_size for f in Path(cand["uri"]).iterdir() if f.is_file()) / 1e6
        p = self.pattern
        llm_cost = cost(settings.heavy_model_id, int(p.avg_input_tokens), int(p.avg_output_tokens))
        lat_sorted = sorted(lat)
        per_req = lambda_cost(lat_sorted[len(lat_sorted) // 2] if lat_sorted else 1.0)
        agreement = round(sum(scores) / len(scores), 4) if scores else 0.0
        coverage = round(answered / len(holdout), 4) if holdout else 0.0
        monthly = 30 * p.frequency_per_day * coverage * max(0.0, llm_cost - per_req)
        metrics = {"agreement": agreement, "coverage": coverage, "user_label_accuracy": user_acc,
                   "holdout": len(holdout), "per_class": {k: round(sum(v) / len(v), 4) for k, v in sorted(per_class.items())},
                   "latency_ms": round(lat_sorted[len(lat_sorted) // 2], 2) if lat_sorted else None,
                   "size_mb": round(size, 3), "cost_per_request": per_req,
                   "projected_net_savings": round(monthly - self.spent(), 4)}
        cand["metrics"] = metrics
        cand["errors"] = errors[:50]
        return {"candidate_id": candidate_id, **metrics}

    def inspect_errors(self, candidate_id: str, k: int = 8) -> list[dict[str, Any]]:
        cand = self.state.get("candidates", {}).get(candidate_id or "")
        if cand is None:
            raise ToolError("unknown candidate (the previous step produced none)")
        errs = cand.get("errors") or []
        wrong = [e for e in errs if e.get("kind", "wrong") == "wrong"]
        confusion = Counter((e["expected"], e["got"]) for e in wrong)
        cand["confusion"] = [{"expected": a, "got": b, "count": n} for (a, b), n in confusion.most_common(10)]
        cand["low_confidence"] = sum(1 for e in errs if e.get("kind") == "low_confidence")
        return sorted(errs, key=lambda e: e.get("kind") != "wrong")[:k]


def prompt_prefixes(prompts: list[str], min_share: float = 0.02) -> list[str]:
    """The instruction openings this pattern's prompts use (every template seen in training, not just one);
    the artifact's input guard defers anything else to the LLM."""
    heads = Counter(p.lower().split("'")[0].split(":")[0].strip()[:20] for p in prompts if p.strip())
    total = sum(heads.values()) or 1
    return sorted(h for h, n in heads.items() if h and n / total >= min_share)


def strip_prefix(label: str, spec: dict[str, Any] | None) -> str:
    """Remove what a feature-source integration adds (e.g. "priority-" for gold customers)."""
    if not spec:
        return label
    for prefix in sorted({str(v) for v in spec.get("map", {}).values()} | {str(spec.get("default", ""))}, key=len,
                         reverse=True):
        if prefix and label.startswith(prefix):
            return label[len(prefix):]
    return label


def wrap_like(example_prompt: str, text: str) -> str:
    """Put a dataset's raw input into the pattern's prompt shape ("Classify this support ticket: '<text>'").
    Inputs that already are full prompts (an instruction followed by a quoted payload) are kept as they are."""
    if re.match(r"^[^'\"]{6,}[:?]\s*['\"].+['\"]\s*\??$", text.strip(), re.S):
        return text
    head = example_prompt.split("'")[0] if "'" in example_prompt else "Classify this support ticket: "
    return f"{head}'{text}'"
