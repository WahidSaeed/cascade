"""A tiny in-process event bus, so lifecycle changes can trigger later-phase work (recognizer
retraining, re-verification) without import cycles."""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger("cascade.hooks")
_handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)


def on(event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _handlers[event].append(fn)
        return fn
    return deco


def fire(event: str, **kwargs: Any) -> None:
    for fn in list(_handlers.get(event, [])):
        try:
            fn(**kwargs)
        except Exception:  # a failing listener must never break the triggering request
            log.exception("hook %s failed in %s", event, getattr(fn, "__name__", fn))
