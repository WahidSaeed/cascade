"""Seed the demo starting state (BUILD.md §17.3).

  python -m app.seed --reset     migrate, wipe and seed the demo world
"""
from __future__ import annotations

import argparse

from . import db
from .demo_world import ALL_USERS, PERSONAS
from .store import store


def seed_users() -> None:
    persona_ids = {p["id"] for p in PERSONAS}
    for u in ALL_USERS:
        store().upsert_user(u["id"], u["name"], u["role"], u["is_admin"], u["id"] in persona_ids, u["groups"])


def reset() -> None:
    db.migrate()
    seed_users()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.parse_args()
    reset()
    print("seeded")


if __name__ == "__main__":
    main()
