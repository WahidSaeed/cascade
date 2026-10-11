"""Several API tasks share one database (Phase 7). Admin edits made through one task (Tools → configuration,
demo reset) reach every other task within `config_refresh_s`: each request checks, at most that often, whether
the stored overrides changed and applies them — including resetting keys that were removed to their defaults."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .config import EDITABLE, Settings, settings

log = logging.getLogger("cascade.tasksync")
_lock = threading.Lock()
_state: dict[str, Any] = {"checked": 0.0, "applied": None, "defaults": None}


def apply(overrides: dict[str, Any]) -> list[str]:
    """Make the editable settings equal defaults + overrides. Returns the keys that changed."""
    if _state["defaults"] is None:
        fresh = Settings()
        _state["defaults"] = {k: getattr(fresh, k) for k in EDITABLE}
    changed = []
    for k in sorted(EDITABLE):
        want = overrides.get(k, _state["defaults"][k])
        if getattr(settings, k) != want:
            setattr(settings, k, want)
            changed.append(k)
    if "llm" in changed:
        from .heavy import set_heavy_model

        set_heavy_model(None)
    _state["applied"] = {k: v for k, v in overrides.items() if k in EDITABLE}
    return changed


def due() -> bool:
    every = settings.config_refresh_s
    return every > 0 and time.monotonic() - _state["checked"] >= every


def maybe_sync() -> None:
    every = settings.config_refresh_s
    now = time.monotonic()
    if every <= 0 or now - _state["checked"] < every:
        return
    with _lock:
        if now - _state["checked"] < every:
            return
        _state["checked"] = now
        from .store import store

        try:
            overrides = {k: v for k, v in store().config_overrides().items() if k in EDITABLE}
        except Exception as exc:  # noqa: BLE001 - e.g. a connection reset by a restore: retry next time
            log.warning("config sync skipped: %s", exc)
            _state["checked"] = 0.0
            return
        if overrides != _state["applied"]:
            apply(overrides)


def mark_applied(overrides: dict[str, Any]) -> None:
    """This task just wrote / applied these overrides itself."""
    _state["applied"] = {k: v for k, v in overrides.items() if k in EDITABLE}
