"""In-process scheduler (local stand-in for EventBridge schedules, BUILD.md §10): daily discovery and an
hourly drift run. In AWS the same jobs are triggered by EventBridge (see infra/terraform/schedules.tf)
calling POST /api/patterns/discover and POST /api/drift/run."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .config import settings

log = logging.getLogger("cascade.scheduler")
JOBS: list[tuple[str, float, Callable[[], object]]] = []
_stop = threading.Event()


def _jobs() -> list[tuple[str, float, Callable[[], object]]]:
    from .discovery import discover
    from .monitor import run_check

    return [("discovery", 24 * 3600.0, discover), ("drift", 3600.0, run_check)]


def start() -> threading.Thread | None:
    if not settings.scheduler:
        return None

    def loop() -> None:
        next_run = {name: time.time() + every for name, every, _ in _jobs()}
        while not _stop.wait(30):
            for name, every, fn in _jobs():
                if time.time() >= next_run[name]:
                    next_run[name] = time.time() + every
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        log.exception("scheduled job %s failed", name)

    t = threading.Thread(target=loop, name="scheduler", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop.set()
