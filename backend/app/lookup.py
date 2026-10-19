"""`lookup` artifacts (BUILD.md §3 rule 1, §4.5): a slot value mapped to a fact from a document table,
e.g. "vacation days in <COUNTRY>".

build_lookup()     the LLM extracts the table from the documents (overhead), the table becomes a rule,
                   the rule declares its `kb_dependencies` (document ids + versions), and it is verified
                   against the RAG pipeline: every fact must agree (threshold 1.0).
lookup_reference() verification reference: retrieve the context for the prompt as a reader of the
                   dependencies, then let the heavy LLM extract the value as structured output.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from . import db
from . import patterns as pat
from .heavy import ContextBlock, LLMRequest, heavy_model, parse_json_reply
from .light.embedder import get_embedder
from .models import ChunkRow, CollectionRow, DocumentRow
from .rag.access import collection_acl
from .rag.retriever import get_retriever, relevant
from .rag.service import select_context
from .schemas import ArtifactKind, KbDependency, Pattern, PatternStatus, TaskType, VerifyResult
from .store import store
from .verifier import Reference, ReferenceFn, record_llm_overhead

VACATION_QUERY = "How many vacation days do employees in {key} get?"


def dependency_principals(collection_ids: list[str]) -> list[str]:
    """Principals that can read the dependency collections (verification reads as such a user)."""
    out: set[str] = set()
    with db.session() as s:
        for c in s.scalars(select(CollectionRow).where(CollectionRow.id.in_(collection_ids))):
            out.update(collection_acl(c.access, c.owner_id, list(c.group_ids or [])))
    return sorted(out)


def _doc_blocks(document_ids: list[str]) -> list[ContextBlock]:
    with db.session() as s:
        rows = s.execute(select(ChunkRow.id, ChunkRow.text, ChunkRow.page, DocumentRow.title, DocumentRow.id,
                                DocumentRow.collection_id, DocumentRow.version)
                         .join(DocumentRow, DocumentRow.id == ChunkRow.document_id)
                         .where(ChunkRow.document_id.in_(document_ids), ChunkRow.doc_version == DocumentRow.version)
                         .order_by(DocumentRow.id, ChunkRow.ord)).all()
    return [ContextBlock(n=i + 1, title=t, text=txt, chunk_id=cid, document_id=did, page=page, collection_id=col,
                         version=v) for i, (cid, txt, page, t, did, col, v) in enumerate(rows)]


def extract_table(document_ids: list[str], schema: str, pattern_id: str | None = None,
                  run_id: str | None = None) -> tuple[dict[str, str], list[KbDependency]]:
    """LLM extracts a structured table from the documents (overhead); records the dependencies."""
    blocks = _doc_blocks(document_ids)
    res = heavy_model().complete(LLMRequest(
        question=f"Extract the table '{schema}' from the passages as a JSON object mapping each key to its value.",
        context=blocks, purpose="extract_table", meta={"schema": schema}))
    record_llm_overhead(res, "compile", pattern_id, run_id, note=f"extract_table {schema}")
    try:
        table = {str(k): str(v) for k, v in parse_json_reply(res.text).items()}
    except (ValueError, AttributeError) as exc:
        raise pat.PatternError(422, f"could not extract a table: {exc}") from exc
    deps = {(b.document_id): KbDependency(document_id=b.document_id, collection_id=b.collection_id,
                                          version=b.version, title=b.title) for b in blocks}
    return table, list(deps.values())


# The reference answer for a lookup is the fact itself, as structured output, so the comparator never has to pick a
# number out of prose (a section number or a citation can come first).
LOOKUP_EXTRACT_SYSTEM = ("Answer only from the numbered context. Reply with one JSON object and nothing else: "
                         '{"value": "<the fact the question asks for, exactly as the context states it>"}, '
                         'or {"value": null} if the context does not state it.')


def lookup_reference(pattern: Pattern, category: str = "verify") -> ReferenceFn:
    slots: dict[str, str] = dict(pattern.model_card.get("lookup", {}).get("slots", {}))
    collection_ids = sorted({d.collection_id for d in pattern.kb_dependencies})
    principals = dependency_principals(collection_ids)
    emb = get_embedder()

    def ref(prompt: str) -> Reference:
        vec, _ = emb.embed(prompt)
        cands = relevant(get_retriever().candidates(vec, prompt, principals, collection_ids, 30))
        chosen = select_context(cands, prompt)
        blocks = [ContextBlock(n=i + 1, title=c.title, text=c.text, chunk_id=c.chunk_id, document_id=c.document_id,
                               page=c.page, collection_id=c.collection_id, version=c.doc_version)
                  for i, (c, _) in enumerate(chosen)]
        slot = slots.get(prompt) or _slot_from(prompt, slots)
        res = heavy_model().complete(LLMRequest(question=prompt, context=blocks, knowledge=True,
                                                system=LOOKUP_EXTRACT_SYSTEM, purpose="lookup_extract",
                                                meta={"slot": slot}))
        record_llm_overhead(res, category, pattern.id, collection_id=collection_ids[0] if collection_ids else None)
        return Reference(res.text, res)
    return ref


def _slot_from(prompt: str, slots: dict[str, str]) -> str:
    """The slot value a (possibly unseen) prompt asks about: the longest known key it mentions."""
    low = prompt.lower()
    keys = sorted(set(slots.values()), key=len, reverse=True)
    return next((k for k in keys if k.lower() in low), "")


def build_lookup(name: str, document_ids: list[str], schema: str = "vacation days by country",
                 query_template: str = VACATION_QUERY, approver: str | None = "eli", user_id: str = "system",
                 pattern_id: str | None = None, extra_templates: list[str] | None = None) -> Pattern:
    from .demo_rules import lookup_rule

    p = Pattern(name=name, description=f"Look up {schema}", task_type=TaskType.lookup, kind=ArtifactKind.rule,
                status=PatternStatus.draft, owner_id=user_id)
    if pattern_id:
        p.id = pattern_id
    table, deps = extract_table(document_ids, schema, p.id)
    if not table:
        raise pat.PatternError(422, "no table rows found in the documents")
    p.handler_code = lookup_rule(table)
    p.kb_dependencies = deps
    examples = [query_template.format(key=k) for k in sorted(table)]
    p.model_card = {"lookup": {"schema": schema, "rows": len(table), "slots": {e: k for e, k in
                                                                               zip(examples, sorted(table), strict=True)}},
                    "kb_dependencies": [f"{d.title} v{d.version}" for d in deps]}
    shapes = examples + [t.format(c=k, key=k) for t in (extra_templates or []) for k in sorted(table)]
    pat.shape_from_examples(p, shapes)
    p.examples = examples                          # facts, not user data: keep them verbatim for re-verification
    store().put_pattern(p)
    result = pat.verify_pattern(p.id, examples, reference=lookup_reference(p))
    if approver and result.passed:
        return pat.approve(p.id, approver)
    return store().get_pattern(p.id) or p


def reverify_lookup(p: Pattern, reason: str) -> VerifyResult:
    """Re-verify a document-backed artifact against the current documents; demote it if it no longer agrees."""
    current = [d.document_id for d in p.kb_dependencies]
    result = pat.verify_pattern(p.id, p.examples, reference=lookup_reference(p, "reverify"), category="reverify")
    if not result.passed:
        pat.demote(p.id, f"{reason}: re-verification agreement {result.score:.0%} — rebuild the lookup from the "
                         f"current documents", kind="reverify_alert")
    else:
        fresh = store().get_pattern(p.id)
        if fresh is not None:
            versions = _current_versions(current)
            fresh.kb_dependencies = [d.model_copy(update={"version": versions.get(d.document_id, d.version)})
                                     for d in fresh.kb_dependencies]
            store().put_pattern(fresh)
    return result


def _current_versions(document_ids: list[str]) -> dict[str, int]:
    from .rag.access import current_versions

    return current_versions(document_ids)


def summary(p: Pattern) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "status": p.status, "dependencies": [d.model_dump() for d in p.kb_dependencies]}
