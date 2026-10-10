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
