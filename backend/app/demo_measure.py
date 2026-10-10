"""S10 — real numbers on Bedrock (BUILD.md §17.2): `make demo-measure` (manual; needs the DEPLOY.md setup).

Replays a seeded 200-prompt sample of the demo traffic through the real router against real Bedrock:
  1. prints a cost estimate first (worst case: every prompt goes to the LLM) and refuses to start if it
     exceeds the hard cap (`--max-usd`, default 5);
  2. replays the sample, stopping as soon as actual spend + overhead reaches the cap;
  3. writes reports/measured.md with the measured token reduction and latency for the deck.
`--dry-run` does steps 1 and 3's plan without any AWS call (this is what the build tests).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, settings
from .heavy import SYSTEM_PROMPT, count_tokens
from .pricing import cost

SAMPLE = 200
EST_OUTPUT_TOKENS = 150


def sample(n: int = SAMPLE, seed: int = 20251020) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in (Path(settings.demo_dir) / "data" / "traffic.jsonl").read_text().splitlines() if x]
    return random.Random(seed).sample(rows, min(n, len(rows)))


def estimate(rows: list[dict[str, Any]], model_id: str) -> dict[str, float]:
    """Worst case: every prompt reaches the LLM with a full context budget for knowledge questions."""
    in_tok = sum(count_tokens(SYSTEM_PROMPT) + count_tokens(r["prompt"]) + 4
                 + (settings.rag_max_context_tokens if r.get("collections") else 0) for r in rows)
    out_tok = EST_OUTPUT_TOKENS * len(rows)
    return {"prompts": len(rows), "input_tokens": in_tok, "output_tokens": out_tok,
            "max_usd": round(cost(model_id, in_tok, out_tok), 4)}


def run(max_usd: float, dry_run: bool = False, n: int = SAMPLE, out: Path | None = None) -> dict[str, Any]:
    rows = sample(n)
    model = settings.bedrock_model_id
    est = estimate(rows, model)
    print(f"Estimate (worst case): {est['prompts']} prompts, ~{est['input_tokens']:,} input + "
          f"{est['output_tokens']:,} output tokens, at most ${est['max_usd']:.2f} on {model}; cap ${max_usd:.2f}")
    report: dict[str, Any] = {"estimate": est, "cap_usd": max_usd, "dry_run": dry_run, "model": model}
    if est["max_usd"] > max_usd:
        report["status"] = "refused: the worst-case estimate exceeds the cap"
        print(report["status"])
        _write(report, out)
        return report
    if dry_run:
        report["status"] = "dry run: no AWS calls made"
        _write(report, out)
        return report
    report.update(_replay(rows, max_usd))
    _write(report, out)
    return report


def _replay(rows: list[dict[str, Any]], max_usd: float) -> dict[str, Any]:  # pragma: no cover - real Bedrock only
    from . import db, engine, seed
    from .ledger import ledger

    settings.llm = "bedrock"
    tmp = tempfile.mkdtemp(prefix="cascade-measure-")
    settings.database_url = f"sqlite:///{tmp}/measure.db"
    settings.data_dir = tmp
    db.reset_engine()
    db.migrate()
    seed.seed_users()
    docs = seed.seed_knowledge()
    seed.install_artifacts(time.time(), docs)
    lat: dict[str, list[float]] = {}
    done = 0
    for r in rows:
        s = ledger().summary()
        if s["actual_cost_usd"] + s["overhead_cost_usd"] >= max_usd:
            break
        cols = [seed.collection_id(k) for k in r.get("collections", [])] or None
        resp = engine.route(r["prompt"], r["user_id"], collection_ids=cols)
        lat.setdefault(resp.route.value, []).append(resp.latency_ms)
        done += 1
    s = ledger().summary()
    return {"status": "measured", "replayed": done, "summary": s,
            "latency_ms": {k: {"p50": round(statistics.median(v), 1), "p95": round(sorted(v)[int(len(v) * .95) - 1], 1),
                               "n": len(v)} for k, v in lat.items() if v}}


def _write(report: dict[str, Any], out: Path | None) -> None:
    out = out or REPO_ROOT / "reports" / "measured.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Measured on Bedrock (S10)", "", f"Model: `{report['model']}` · cap ${report['cap_usd']:.2f} · "
             f"status: **{report['status']}**", "",
             f"Estimate (worst case): {report['estimate']['prompts']} prompts, at most "
             f"${report['estimate']['max_usd']:.2f}", ""]
    s = report.get("summary")
    if s:
        lines += ["| Metric | Value |", "|---|---|",
                  f"| Requests replayed | {report['replayed']} |",
                  f"| Token reduction | {s['token_reduction']:.1%} |",
                  f"| Net saved (after overhead) | ${s['net_saved_usd']:.4f} |",
                  f"| Actual spend | ${s['actual_cost_usd']:.4f} |",
                  f"| Routes | {s['by_route']} |"]
        lines += ["", "| Route | p50 latency | p95 latency | n |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['p50']} ms | {v['p95']} ms | {v['n']} |" for k, v in report["latency_ms"].items()]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n", type=int, default=SAMPLE)
    a = ap.parse_args(argv)
    r = run(a.max_usd, a.dry_run, a.n)
    return 0 if not str(r["status"]).startswith("refused") else 1


if __name__ == "__main__":
    sys.exit(main())
