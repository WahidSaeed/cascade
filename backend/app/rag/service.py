"""Knowledge step of the router (BUILD.md §4.5). Phase 1: no documents exist yet, so a
knowledge-scoped request has the user's principals but no candidates and no context.
Phase 3 adds retrieval, fingerprints and context assembly."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..heavy import ContextBlock
from ..store import store

if TYPE_CHECKING:
    from ..engine import Ctx, Knowledge


def prepare_knowledge(ctx: Ctx, collection_ids: list[str]) -> Knowledge:
    from ..engine import Knowledge

    return Knowledge(collection_ids=list(collection_ids), principals=store().principals(ctx.user_id))


def build_context(knowledge: Knowledge, question: str) -> list[ContextBlock]:
    return []
