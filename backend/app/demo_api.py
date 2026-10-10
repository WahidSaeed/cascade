"""Demo control endpoints (BUILD.md §17.3). Mounted only when CASCADE_DEMO=true."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter

from .config import settings
from .store import store

router = APIRouter(prefix="/api/demo", tags=["demo"])
_lock = threading.Lock()


@router.get("/state")
def state() -> dict[str, Any]:
    return {"demo": settings.demo, "seed": store().config_overrides().get("_seed"),
            "latency_ms": settings.demo_latency_ms}


@router.post("/reset")
def reset() -> dict[str, Any]:
    """Return to the demo starting state (restores the seeded snapshot; < 30 s)."""
    from . import seed
    from .config import EDITABLE, Settings
    from .main import apply_config_overrides

    with _lock:
        out = seed.reset()
        fresh = Settings()                       # Tools edits made during the demo are undone too
        for k in EDITABLE:
            setattr(settings, k, getattr(fresh, k))
        apply_config_overrides()
        from .light.cache import light_cache
        from .runtime import warm

        light_cache().ensure_loaded()
        warm(store().approved_patterns())
    return out


FAULTY_EXTRACTION = """
import json
import re


def handle(prompt: str) -> str:
    \"\"\"A silently regressed build: drops the cents and misreads the currency.\"\"\"
    m = re.search(r"['\\"](.+)['\\"]\\s*$", prompt.strip(), re.S)
    text = m.group(1) if m else prompt
    order = re.search(r"\\bNP-\\d{4,6}\\b", text, re.I)
    email = re.search(r"\\b[\\w.+-]+@[\\w-]+\\.[\\w.]+\\b", text)
    amt = re.search(r"(\\d+)", text.split("@")[-1])
    return json.dumps({"order_number": order.group(0).upper() if order else None,
                       "email": email.group(0).lower() if email else None,
                       "amount": amt.group(1) + ".00" if amt else None, "currency": "USD"}, sort_keys=True)
"""


@router.post("/inject-faulty")
def inject_faulty(pattern_name: str = "Order details extraction") -> dict[str, Any]:
    """S8: swap the *deployed* extraction artifact for a version that misreads amounts (a silent regression
    the review never saw). Its status stays approved — only drift sampling can catch it."""
    from .runtime import artifact_dir, runtime

    p = next((x for x in store().approved_patterns() if x.name == pattern_name), None)
    if p is None:
        from fastapi import HTTPException

        raise HTTPException(404, f"no approved pattern named {pattern_name!r}")
    d = artifact_dir(p)
    d.mkdir(parents=True, exist_ok=True)
    from .runtime import materialize

    materialize(p)
    (d / "handler.py").write_text(FAULTY_EXTRACTION.strip() + "\n")
    runtime().stop(p.id)                  # the next call loads the faulty build
    return {"pattern_id": p.id, "injected": True}


@router.post("/drift-check")
def drift_check() -> list[dict[str, Any]]:
    from .monitor import run_check

    return run_check(n=20)


@router.post("/discover")
def demo_discover() -> list[dict[str, Any]]:
    from .discovery import discover

    return [{"id": p.id, "name": p.name, "task_type": p.task_type, "projected_tokens_per_day": p.projected_tokens_per_day}
            for p in discover()]


@router.post("/upload-handbook-v2")
def demo_upload_v2() -> dict[str, Any]:
    from .seed import upload_handbook_v2

    return upload_handbook_v2()


@router.post("/rebuild-lookup")
def rebuild_lookup() -> dict[str, Any]:
    """S5: rebuild the vacation-days lookup from the CURRENT handbook — a real agent run (extract_table ->
    lookup rule -> evaluate), the verification gate against the RAG pipeline, then Eli's approval."""
    from . import patterns as pat
    from .agent import runner
    from .lookup import lookup_reference
    from .seed import collection_id

    p = next((x for x in store().list_patterns() if x.name == "Vacation days by country"), None)
    if p is None:
        from fastapi import HTTPException

        raise HTTPException(404, "no vacation lookup pattern")
    from .rag.ingest import list_documents

    handbook = next(d for d in list_documents(collection_id("handbook")) if d["title"] == "Employee Handbook")
    run = runner.create_run(p.id, "chen", {"document_ids": [handbook["id"]], "collection_ids": [collection_id("handbook")],
                                           "instructions": "Rebuild from the current handbook"})
    run = runner.start_run(run["id"], sync=True)
    run = runner.respond(run["id"], "accept", "", "chen")
    if run["status"] != "accepted":
        return {"rebuilt": False, "status": run["status"], "message": run["events"][-1]["text"]}
    fresh = store().get_pattern(p.id)
    assert fresh is not None
    result = pat.verify_pattern(p.id, fresh.examples, reference=lookup_reference(fresh))
    if not result.passed:
        return {"rebuilt": False, "status": "verification_failed", "score": result.score}
    approved = pat.approve(p.id, "eli")
    return {"rebuilt": True, "pattern_id": p.id, "version": approved.version,
            "dependencies": [f"{d.title} v{d.version}" for d in approved.kb_dependencies]}
