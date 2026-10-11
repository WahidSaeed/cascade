"""`cascade-agent` worker (BUILD.md §11.3): runs agent steps outside the API (an ECS Fargate task in AWS,
the `agent-worker` container locally). It picks up any `agent_running` run whose lease is free or expired,
so a run whose worker was killed resumes from its last checkpoint."""
from __future__ import annotations

import logging
import time

from .. import db
from .runner import execute, runnable

log = logging.getLogger("cascade.agent.worker")


def poll_once() -> int:
    n = 0
    for run_id in runnable():
        execute(run_id)
        n += 1
    return n


def serve(iterations: int | None = None, idle_s: float = 2.0) -> None:
    """Poll forever. An error (e.g. connections dropped by a database failover or a demo reset) is logged and the
    loop carries on: the run it was working on keeps its checkpoint and resumes once its lease expires."""
    n = 0
    while iterations is None or n < iterations:
        n += 1
        try:
            if poll_once():
                continue
        except Exception:  # noqa: BLE001 - never let one bad poll stop the worker
            log.exception("agent worker poll failed; retrying")
        time.sleep(idle_s)


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    db.migrate()
    serve()


if __name__ == "__main__":
    main()
