"""Seed the demo starting state (BUILD.md §17.3).

  python -m app.seed --reset     restore the demo starting state (from the snapshot; builds it if missing)
  python -m app.seed --build     rebuild the starting state from scratch and snapshot it

Building replays the generated week of traffic (demo/data/traffic.jsonl) through the real
router, so every ledger entry, cache entry and log row is produced by the same code that serves
live traffic. Artifacts are installed through the normal compile -> verify -> approve gate.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import update

from . import db
from . import demo_world as w
from .config import settings
from .models import OverheadRow
from .state import reset_runtime_state
from .store import store

SEED_VERSION = "phase1-v1"
DAY = 86400


def traffic() -> list[dict[str, Any]]:
    path = Path(settings.demo_dir) / "data" / "traffic.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def seed_users() -> None:
    persona_ids = {p["id"] for p in w.PERSONAS}
    for u in w.ALL_USERS:
        store().upsert_user(u["id"], u["name"], u["role"], u["is_admin"], u["id"] in persona_ids, u["groups"])


def _install_rule(name: str, task_type: str, examples: list[str], at: float) -> str:
    from . import patterns as pat
    from .schemas import CompileRequest

    p = pat.compile_pattern(CompileRequest(name=name, description=name, task_type=task_type, examples=examples))
    res = pat.verify_pattern(p.id, examples[:40])
    if not res.passed:
        raise RuntimeError(f"seed artifact {name} failed verification: {res.score}")
    pat.approve(p.id, "eli")
    with db.session() as s:   # the build happened at the start of the simulated week
        s.execute(update(OverheadRow).where(OverheadRow.pattern_id == p.id).values(ts=at, simulated=True))
    return p.id


def install_artifacts(week_start: float) -> dict[str, str]:
    rows = traffic()
    ex = [r["prompt"] for r in rows if r["task"] == "extraction"][:60]
    conv = [r["prompt"] for r in rows if r["task"] == "conversion"][:60]
    return {
        "extraction": _install_rule("Order details extraction", "extraction", ex, week_start - 3600),
        "conversion": _install_rule("Unit conversion", "transform", conv, week_start - 3000),
    }


def replay_week(week_start: float) -> int:
    """Route the generated week through the real router at its simulated timestamps."""
    from .engine import route

    n = 0
    for r in traffic():
        route(r["prompt"], r["user_id"], now=week_start + r["t"], simulated=True)
        n += 1
    return n


def build() -> dict[str, Any]:
    t0 = time.time()
    db.wipe()
    reset_runtime_state()
    seed_users()
    week_start = time.time() - 7 * DAY
    random.seed(20251020)
    ids = install_artifacts(week_start)
    n = replay_week(week_start)
    info = {"seed_version": SEED_VERSION, "built_at": time.time(), "requests": n, "patterns": ids,
            "seconds": round(time.time() - t0, 1)}
    store().set_config("_seed", info)
    snapshot_save()
    return info


# ----------------------------------------------------------------------------- snapshots
def _snapshot_dir() -> Path:
    return Path(settings.data_dir) / "snapshot"


def snapshot_save() -> None:
    d = _snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    if settings.is_postgres:
        db.pg_snapshot_save()
    else:
        db.reset_engine()
        src = Path(settings.resolved_database_url().removeprefix("sqlite:///"))
        shutil.copy2(src, d / "cascade.db")
    art = Path(settings.data_dir) / "artifacts"
    if (d / "artifacts").exists():
        shutil.rmtree(d / "artifacts")
    if art.exists():
        shutil.copytree(art, d / "artifacts")
    (d / "VERSION").write_text(SEED_VERSION)


def snapshot_ok() -> bool:
    v = _snapshot_dir() / "VERSION"
    files_ok = v.exists() and v.read_text() == SEED_VERSION
    if settings.is_postgres:
        return files_ok and db.pg_snapshot_exists(SEED_VERSION)
    return files_ok


def snapshot_restore() -> None:
    reset_runtime_state()
    d = _snapshot_dir()
    if settings.is_postgres:
        db.pg_snapshot_restore()
    else:
        db.reset_engine()
        dst = Path(settings.resolved_database_url().removeprefix("sqlite:///"))
        for suffix in ("", "-wal", "-shm"):
            Path(str(dst) + suffix).unlink(missing_ok=True)
        shutil.copy2(d / "cascade.db", dst)
    art = Path(settings.data_dir) / "artifacts"
    if art.exists():
        shutil.rmtree(art)
    if (d / "artifacts").exists():
        shutil.copytree(d / "artifacts", art)
    reset_runtime_state()


def reset() -> dict[str, Any]:
    t0 = time.time()
    if snapshot_ok():
        snapshot_restore()
        db.migrate()
        return {"restored": True, "seconds": round(time.time() - t0, 1)}
    info = build()
    snapshot_restore()
    return {"restored": False, **info}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="restore the demo starting state")
    ap.add_argument("--build", action="store_true", help="rebuild the starting state and snapshot it")
    args = ap.parse_args()
    out = build() if args.build else reset()
    from .runtime import _reset

    _reset()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
