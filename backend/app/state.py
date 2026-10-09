"""Registry of in-process runtime state (indexes, loaded models) that must be rebuilt when the
database changes: tests switch databases, and demo reset restores a snapshot."""
from __future__ import annotations

from collections.abc import Callable

_resetters: list[Callable[[], None]] = []


def on_reset(fn: Callable[[], None]) -> Callable[[], None]:
    _resetters.append(fn)
    return fn


def reset_runtime_state() -> None:
    for fn in list(_resetters):
        fn()
