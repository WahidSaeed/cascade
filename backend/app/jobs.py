"""Scheduled jobs as a CLI, for EventBridge -> ECS RunTask in AWS (infra/terraform/schedules.tf).

  python -m app.jobs discover     nightly discovery
  python -m app.jobs drift        drift check of every approved artifact
"""
from __future__ import annotations

import json
import sys

from . import db


def main(argv: list[str]) -> int:
    db.migrate()
    job = argv[0] if argv else ""
    if job == "discover":
        from .discovery import discover

        print(json.dumps([p.name for p in discover()]))
    elif job == "drift":
        from .monitor import run_check

        print(json.dumps(run_check(), default=str))
    else:
        print("usage: python -m app.jobs discover|drift", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
