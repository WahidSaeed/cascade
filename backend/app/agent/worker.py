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


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    db.migrate()
    while True:
        if not poll_once():
            time.sleep(2)


if __name__ == "__main__":
    main()
