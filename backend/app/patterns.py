"""Pattern lifecycle: compile -> verify -> approve (deploy) -> serve; edit/reject/demote tear down.

Rules enforced here:
  1  only verifiable task types compile;
  3  two locks before serving: verification score >= threshold AND human approval; deploy must
     succeed; any change sends the artifact back to `draft` and tears down its Lambda;
  6  the compile step flags literal user data copied from logged prompts (`leak_warnings`);
  5  compile and verify LLM calls are ledger overhead.
"""
from __future__ import annotations

import ast
import re
import time
from typing import Any

from sqlalchemy import select

from . import db
from .config import settings
from .deploy import DeployError, deploy, teardown
from .heavy import LLMRequest, heavy_model
from .light.embedder import get_embedder
from .light.normalize import normalize
from .light.recognizer import refresh_recognizer
from .light.vec import centroid
from .models import Notification, RequestLog
from .privacy import contains_pii, redact
from .schemas import (
    COMPILABLE_TASK_TYPES,
    ArtifactKind,
    CompileRequest,
    KbDependency,
    Pattern,
    PatternEdit,
    PatternStatus,
    TaskType,
    VerifyResult,
)
from .store import store
from .verifier import ReferenceFn, record_llm_overhead, threshold_for, verify

COMPILE_SYSTEM = (
    "You write small, dependency-free, deterministic Python handlers that reproduce a narrow task an LLM "
    "was doing. Output ONLY Python source defining `def handle(prompt: str) -> str`. Standard library "
    "only; no network, file or OS access; never embed names, emails, IDs or text copied from the "
    "examples. If the input does not fit the task, return the literal string '__FALLBACK__'."
)
MAX_EXEMPLARS = 24


class PatternError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _extract_code(text: str) -> str:
    fence = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (fence.group(1) if fence else text).strip() + "\n"


# ----------------------------------------------------------------------------- leak check (rule 6)
_DATAISH = re.compile(r"@|\d{3,}|[A-Z]{2,}-\d+")


def leak_warnings(code: str, logged_prompts: list[str]) -> list[str]:
    """String literals in generated code that look like user data and appear verbatim in logs."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"code does not parse: {exc}"]
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    warnings = []
    for lit in sorted(literals):
        s = lit.strip()
        if len(s) < 4 or not (_DATAISH.search(s) or contains_pii(s) or len(s) >= 24):
            continue
        if any(s in p for p in logged_prompts):
            warnings.append(f"literal {s[:60]!r} appears verbatim in logged prompts")
    return warnings


def _logged_prompts(limit: int = 5000) -> list[str]:
    with db.session() as s:
        return list(s.scalars(select(RequestLog.prompt).order_by(RequestLog.ts.desc()).limit(limit)))


# ----------------------------------------------------------------------------- shape of a pattern
def shape_from_examples(pattern: Pattern, examples: list[str]) -> None:
    """Templates, centroid and exemplar embeddings for the recognizer (redacted examples only)."""
    if not examples:
        return
    emb = get_embedder()
    vecs = emb.embed_many(examples[:200])
    pattern.centroid = centroid(vecs)
    pattern.exemplars = vecs[:MAX_EXEMPLARS]
    norms = [normalize(e) for e in examples]
    pattern.template_keys = sorted({n.template_key for n in norms})
    pattern.templates = sorted({n.template for n in norms})[:50]
    pattern.examples = [redact(e) for e in examples[:50]]


# ----------------------------------------------------------------------------- compile
def compile_pattern(req: CompileRequest, user_id: str = "system", meta: dict[str, Any] | None = None,
                    run_id: str | None = None) -> Pattern:
    if req.task_type not in COMPILABLE_TASK_TYPES:
        raise PatternError(422, f"task type {req.task_type!r} is not compilable: only verifiable task types "
                                f"{sorted(COMPILABLE_TASK_TYPES)} may become artifacts (rule 1)")
    task_type = TaskType(req.task_type)
    if task_type == TaskType.lookup and not req.kb_dependencies:
        raise PatternError(422, "lookup patterns must declare kb_dependencies")
    if req.handler_code:
        code = req.handler_code
    else:
        llm_meta = {"examples": req.examples[:20], "task_type": req.task_type, **(meta or {})}
        prompt = (f"Task: {req.description}\nTask type: {req.task_type}\n\nExample prompts this handler must cover:\n"
                  + "\n".join(f"- {redact(e)}" for e in req.examples[:20]) + "\n\nWrite the handler now.")
        res = heavy_model().complete(LLMRequest(question=prompt, system=COMPILE_SYSTEM, purpose="compile",
                                                meta=llm_meta))
        code = _extract_code(res.text)
        pattern_id_hint = (meta or {}).get("pattern_id")
        record_llm_overhead(res, "compile", pattern_id_hint, run_id, note=req.name)
    p = Pattern(name=req.name, description=req.description, task_type=task_type, kind=ArtifactKind.rule,
                handler_code=code, status=PatternStatus.draft, owner_id=user_id,
                kb_dependencies=list(req.kb_dependencies))
    if meta and meta.get("pattern_id"):
        p.id = str(meta["pattern_id"])
    shape_from_examples(p, req.examples)
    p.leak_warnings = leak_warnings(code, _logged_prompts())
    return store().put_pattern(p)


# ----------------------------------------------------------------------------- verify
def verify_pattern(pattern_id: str, samples: list[str] | None = None, reference: ReferenceFn | None = None,
                   expected: dict[str, str] | None = None, category: str = "verify") -> VerifyResult:
    p = _get(pattern_id)
    samples = samples or p.examples
    if not samples:
        raise PatternError(422, "no samples to verify against")
    result = verify(p, samples, reference=reference, expected=expected, category=category)
    p = _get(pattern_id)   # re-read: verification can take a while
    p.verification = result
    p.verification_score = result.score
    if result.avg_input_tokens is not None and result.avg_output_tokens is not None:
        n_old, n_new = p.samples_measured, result.sample_size
        tot = n_old + n_new
        p.avg_input_tokens = round((p.avg_input_tokens * n_old + result.avg_input_tokens * n_new) / tot, 2)
        p.avg_output_tokens = round((p.avg_output_tokens * n_old + result.avg_output_tokens * n_new) / tot, 2)
        p.samples_measured = tot
    store().put_pattern(p)
    return result


# ----------------------------------------------------------------------------- approve / edit / reject
def approve(pattern_id: str, approver: str) -> Pattern:
    p = _get(pattern_id)
    need = threshold_for(p)
    if p.verification is None or p.verification_score is None or not p.verification.passed \
            or p.verification_score < need:
        raise PatternError(409, f"cannot approve: verification score {p.verification_score} below required {need}. "
                                "Verify first.")
    if p.status == PatternStatus.approved:
        return p
    try:
        fn = deploy(p)
    except (DeployError, FileNotFoundError) as exc:
        raise PatternError(502, f"deploy failed, pattern not approved: {exc}") from exc
    p.deployed_function = fn
    p.status = PatternStatus.approved
    p.approved_by = approver
    p.alert = None
    store().put_pattern(p)
    refresh_recognizer()
    _on_lifecycle_change()
    return p


def edit(pattern_id: str, change: PatternEdit) -> Pattern:
    p = _get(pattern_id)
    data = change.model_dump(exclude_none=True)
    was_approved = p.status == PatternStatus.approved
    for k, v in data.items():
        setattr(p, k, v)
    if "handler_code" in data or "task_type" in data:
        p.version += 1
        p.verification = None
        p.verification_score = None
        p.status = PatternStatus.draft
        p.leak_warnings = leak_warnings(p.handler_code, _logged_prompts())
        if was_approved:
            teardown(p.id)
            p.deployed_function = None
    store().put_pattern(p)
    if was_approved or "handler_code" in data or "task_type" in data:
        refresh_recognizer()
        _on_lifecycle_change(p.id)
    return p


def reject(pattern_id: str) -> Pattern:
    p = _get(pattern_id)
    if p.status == PatternStatus.approved:
        teardown(p.id)
        p.deployed_function = None
    p.status = PatternStatus.rejected
    store().put_pattern(p)
    refresh_recognizer()
    _on_lifecycle_change(p.id)
    return p


def demote(pattern_id: str, reason: str, kind: str = "drift_alert") -> Pattern:
    """Take an artifact out of service (drift / document change): `shadow`, torn down, alerted."""
    p = _get(pattern_id)
    if p.status == PatternStatus.approved:
        teardown(p.id)
        p.deployed_function = None
    p.status = PatternStatus.shadow
    p.alert = reason
    store().put_pattern(p)
    refresh_recognizer()
    _on_lifecycle_change(p.id)
    with db.session() as s:
        s.add(Notification(user_id=None, kind=kind, title=f"{p.name} was demoted", body=reason,
                           link=f"/patterns/?id={p.id}", ref_id=p.id, created_at=time.time()))
    return p


def install_artifact(p: Pattern, approver: str, verification: VerifyResult) -> Pattern:
    """Seed/agent helper: store a verified artifact and approve it through the normal gate."""
    p.verification = verification
    p.verification_score = verification.score
    store().put_pattern(p)
    return approve(p.id, approver)


def _on_lifecycle_change(pattern_id: str | None = None) -> None:
    """A pattern changed state: its cached artifact answers go (rule 3), and the trained recognizer
    retrains (Phase 4)."""
    from . import hooks
    from .light.cache import light_cache

    if pattern_id:
        light_cache().invalidate_pattern(pattern_id)

    hooks.fire("pattern_lifecycle")


def _get(pattern_id: str) -> Pattern:
    p = store().get_pattern(pattern_id)
    if p is None:
        raise PatternError(404, "pattern not found")
    return p


def deps_readable(p: Pattern, principals: list[str]) -> bool:
    """Rule 13: a document-backed artifact serves only users who can read all its dependencies."""
    if not p.kb_dependencies:
        return True
    from .rag.access import can_read_documents

    return can_read_documents([d.document_id for d in p.kb_dependencies], principals)


def deps_current(p: Pattern) -> bool:
    """Defence in depth: never serve a lookup built from a document version that is no longer current."""
    if not p.kb_dependencies:
        return True
    from .rag.access import current_versions

    cur = current_versions([d.document_id for d in p.kb_dependencies])
    return all(cur.get(d.document_id) == d.version for d in p.kb_dependencies)


__all__ = ["VerifyResult", "PatternError", "compile_pattern", "verify_pattern", "approve", "edit", "reject", "demote",
           "install_artifact", "deps_readable", "deps_current", "KbDependency", "settings"]
