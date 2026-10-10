"""The router (BUILD.md §4.1, rule 4): exact cache -> semantic cache -> recognized pattern's
artifact -> heavy LLM, with safe fallback. Knowledge-scoped prompts run candidate retrieval
first, because both cache keys include the context fingerprint (§4.5). Context-dependent
follow-ups skip the caches and artifacts. Every request writes exactly one ledger entry (rule 5).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from . import db
from .config import settings
from .heavy import SYSTEM_PROMPT, ContextBlock, LLMRequest, LLMResult, count_tokens, heavy_model
from .ledger import ESTIMATE_METHODS, LedgerEntry
from .light.cache import GLOBAL, CachedAnswer, light_cache
from .light.embedder import get_embedder
from .light.metrics import light_metrics
from .light.normalize import Normalized, normalize
from .light.recognizer import recognizer
from .models import LedgerRow, RequestLog, Turn
from .pricing import cost, embed_cost, lambda_cost
from .privacy import contains_pii, redact
from .runtime import ArtifactResult, runtime
from .schemas import ChatResponse, LightInfo, Pattern, Route, Source, TokenInfo
from .store import store

MESSAGE_OVERHEAD_TOKENS = 4


@dataclass
class Knowledge:
    """What the knowledge step produced for one request (Phase 3)."""
    collection_ids: list[str]
    principals: list[str]
    candidates: list[Any] = field(default_factory=list)
    fingerprint: str | None = None
    retrieval_query: str = ""


@dataclass
class Ctx:
    request_id: str
    user_id: str
    conversation_id: str
    prompt: str
    norm: Normalized
    history: list[dict[str, Any]]
    t0: float
    now: float
    simulated: bool
    stages: dict[str, float] = field(default_factory=dict)
    vector: list[float] | None = None
    embed_tokens: int = 0
    knowledge: Knowledge | None = None


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 3)


def route(prompt: str, user_id: str = "anonymous", conversation_id: str | None = None,
          collection_ids: list[str] | None = None, now: float | None = None, simulated: bool = False) -> ChatResponse:
    t0 = time.perf_counter()
    conv_id, conv_cols, history = store().open_conversation(conversation_id, user_id, collection_ids)
    cols = list(collection_ids) if collection_ids is not None else conv_cols
    t = time.perf_counter()
    norm = normalize(prompt, has_history=bool(history), short_words=settings.followup_short_words)
    ctx = Ctx(request_id=f"req_{uuid.uuid4().hex[:16]}", user_id=user_id, conversation_id=conv_id, prompt=prompt,
              norm=norm, history=history, t0=t0, now=now or time.time(), simulated=simulated)
    ctx.stages["normalize"] = _ms(t)

    if cols:
        from .rag.service import prepare_knowledge

        ctx.knowledge = prepare_knowledge(ctx, cols)

    fingerprint = ctx.knowledge.fingerprint if ctx.knowledge else None
    use_cache = not norm.context_dependent and (not norm.volatile or settings.volatile_ttl_s > 0)

    # 2. exact cache: instant and free, so it runs before embedding
    if use_cache:
        t = time.perf_counter()
        hit = light_cache().lookup_exact(norm, user_id, fingerprint)
        ctx.stages["exact_cache"] = _ms(t)
        if hit:
            return _serve_cache(ctx, hit, "exact_cache")

    # 3. embed (reused from retrieval for knowledge-scoped prompts)
    _ensure_vector(ctx)

    if not norm.context_dependent:
        # 4. semantic cache: nearest neighbour + equal slots + same context fingerprint
        if use_cache:
            t = time.perf_counter()
            assert ctx.vector is not None
            hit = light_cache().lookup_semantic(norm, ctx.vector, user_id, fingerprint)
            ctx.stages["semantic_cache"] = _ms(t)
            if hit:
                return _serve_cache(ctx, hit, "semantic_cache")
        # 5. pattern recognizer -> artifact
        t = time.perf_counter()
        assert ctx.vector is not None
        rec = recognizer().recognize(norm, ctx.vector)
        ctx.stages["recognizer"] = _ms(t)
        if rec.pattern_id:
            pattern = store().get_pattern(rec.pattern_id)
            if pattern is not None and _artifact_allowed(ctx, pattern):
                t = time.perf_counter()
                res = runtime().invoke(pattern, prompt)
                ctx.stages["artifact"] = _ms(t)
                if res.usable:
                    return _serve_artifact(ctx, pattern, res, rec.prob, rec.margin)
                return _serve_llm(ctx, LightInfoExtra("recognizer", rec.prob, rec.margin, fallback=True,
                                                      reason=f"artifact fell back: {res.error or res.result} "
                                                             f"(confidence {res.confidence:.2f})"))
            return _serve_llm(ctx, LightInfoExtra("recognizer", rec.prob, rec.margin,
                                                  reason="pattern not servable for this user or document version"))
        return _serve_llm(ctx, LightInfoExtra("none", rec.prob, rec.margin, reason="no cache hit, no pattern match"))
    return _serve_llm(ctx, LightInfoExtra("skipped", None, None,
                                          reason="follow-up depends on earlier turns: caches and artifacts skipped"))


@dataclass
class LightInfoExtra:
    stage: str
    prob: float | None
    margin: float | None
    fallback: bool = False
    reason: str = ""


def _ensure_vector(ctx: Ctx) -> None:
    if ctx.vector is None:
        t = time.perf_counter()
        emb = get_embedder()
        ctx.vector, tokens = emb.embed(ctx.prompt)
        ctx.embed_tokens = tokens if emb.paid else 0
        ctx.stages["embed"] = _ms(t)


def _artifact_allowed(ctx: Ctx, pattern: Pattern) -> bool:
    from .patterns import deps_current, deps_readable

    principals = ctx.knowledge.principals if ctx.knowledge else store().principals(ctx.user_id)
    return deps_readable(pattern, principals) and deps_current(pattern)


def _light(ctx: Ctx, stage: str, prob: float | None = None, margin: float | None = None,
           similarity: float | None = None, reason: str = "") -> LightInfo:
    return LightInfo(stage_hit=stage, template=ctx.norm.template, slots=ctx.norm.slots, prob=prob, margin=margin,
                     similarity=similarity, volatile=ctx.norm.volatile, context_dependent=ctx.norm.context_dependent,
                     knowledge=ctx.knowledge is not None,
                     context_fingerprint=ctx.knowledge.fingerprint if ctx.knowledge else None, reason=reason,
                     latency_ms_by_stage=dict(ctx.stages))


def _embed_usd(ctx: Ctx) -> float:
    return embed_cost(get_embedder().model_id, ctx.embed_tokens) if ctx.embed_tokens else 0.0


# ----------------------------------------------------------------------------- cache
def _serve_cache(ctx: Ctx, hit: CachedAnswer, stage: str) -> ChatResponse:
    u = hit.usage
    if u.get("origin") == "artifact":
        # an artifact's answer, cached: the avoided LLM usage is the artifact's own estimate
        avoided_in, avoided_out = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        method = str(u.get("estimate_method", "heuristic"))
    else:
        reused = int(u.get("context_tokens", 0)) + int(u.get("history_tokens", 0)) + int(u.get("system_tokens", 0))
        avoided_in = count_tokens(ctx.prompt) + MESSAGE_OVERHEAD_TOKENS + reused
        avoided_out = int(u.get("output_tokens", 0))
        method = "cache_exact"
    avoided_usd = cost(hit.model_id or settings.heavy_model_id, avoided_in, avoided_out)
    sources = [Source.model_validate(s) for s in hit.sources]
    entry = LedgerEntry(request_id=ctx.request_id, user_id=ctx.user_id, route=Route.cache.value, ts=ctx.now,
                        model_id=hit.model_id, embed_tokens=ctx.embed_tokens, embed_cost_usd=_embed_usd(ctx), cost_actual_usd=_embed_usd(ctx),
                        tokens_avoided_in=avoided_in, tokens_avoided_out=avoided_out,
                        context_avoided_tokens=int(u.get("context_tokens", 0)), cost_avoided_usd=avoided_usd,
                        estimate_method=method, simulated=ctx.simulated, pattern_id=u.get("pattern_id"),
                        collection_ids=ctx.knowledge.collection_ids if ctx.knowledge else [],
                        context_fingerprint=hit.context_fingerprint, sources=hit.sources)
    light = _light(ctx, stage, similarity=hit.similarity,
                   reason=("exact repeat" if stage == "exact_cache" else f"reworded repeat (cosine {hit.similarity:.3f}), "
                           "slots equal") + (" · same sources" if hit.context_fingerprint else "")
                   + (" · shared entry" if hit.scope == GLOBAL else ""))
    tokens = TokenInfo(tokens_avoided=avoided_in + avoided_out, cost_avoided_usd=avoided_usd,
                       cost_actual_usd=entry.cost_actual_usd, estimate_method=method)
    return _finish(ctx, entry, Route.cache, hit.answer, light, tokens, sources=sources, confidence=hit.similarity)


# ----------------------------------------------------------------------------- artifact
def _serve_artifact(ctx: Ctx, pattern: Pattern, res: ArtifactResult, prob: float, margin: float) -> ChatResponse:
    if pattern.samples_measured > 0:
        avoided_in, avoided_out, method = int(round(pattern.avg_input_tokens)), int(round(pattern.avg_output_tokens)), \
            "pattern_measured"
    else:
        avoided_in = count_tokens(SYSTEM_PROMPT) + count_tokens(ctx.prompt) + MESSAGE_OVERHEAD_TOKENS
        avoided_out = count_tokens(res.result or "") + settings.demo_reasoning_overhead_tokens
        method = "heuristic"
    avoided_usd = cost(settings.heavy_model_id, avoided_in, avoided_out)
    runtime_usd = lambda_cost(res.latency_ms)
    sources = [Source(n=i + 1, document_id=d.document_id, title=d.title or "Source", chunk_id="",
                      collection_id=d.collection_id, version=d.version)
               for i, d in enumerate(pattern.kb_dependencies)]
    entry = LedgerEntry(request_id=ctx.request_id, user_id=ctx.user_id, route=Route.artifact.value, ts=ctx.now,
                        pattern_id=pattern.id, artifact_kind=pattern.kind.value if pattern.kind else None,
                        embed_tokens=ctx.embed_tokens, embed_cost_usd=_embed_usd(ctx),
                        cost_actual_usd=round(runtime_usd + _embed_usd(ctx), 10),
                        runtime_cost_usd=runtime_usd, tokens_avoided_in=avoided_in, tokens_avoided_out=avoided_out,
                        cost_avoided_usd=avoided_usd, estimate_method=method, simulated=ctx.simulated,
                        collection_ids=ctx.knowledge.collection_ids if ctx.knowledge else [],
                        sources=[s.model_dump() for s in sources])
    light = _light(ctx, "recognizer", prob=prob, margin=margin,
                   reason=f"recognized “{pattern.name}” (p={prob:.2f}, margin {margin:.2f}); artifact confidence "
                          f"{res.confidence:.2f}")
    tokens = TokenInfo(tokens_avoided=avoided_in + avoided_out, cost_avoided_usd=avoided_usd,
                       cost_actual_usd=entry.cost_actual_usd, estimate_method=method)
    pattern_hit(pattern.id)
    if not pattern.kb_dependencies and not ctx.norm.context_dependent:
        # Remember the artifact's answer too (saves the next invocation). Never for document-backed
        # artifacts: their answers must follow document changes (rule 13).
        usage = {"origin": "artifact", "pattern_id": pattern.id, "input_tokens": avoided_in,
                 "output_tokens": avoided_out, "estimate_method": method}
        scope = GLOBAL if settings.global_cache and not contains_pii(ctx.prompt) else "user"
        light_cache().put(ctx.norm, ctx.prompt, ctx.vector, res.result or "", ctx.user_id, scope, usage,
                          settings.heavy_model_id, now=ctx.now, pattern_id=pattern.id)
    return _finish(ctx, entry, Route.artifact, res.result or "", light, tokens, sources=sources,
                   confidence=res.confidence, pattern=pattern)


def pattern_hit(pattern_id: str) -> None:
    from sqlalchemy import update

    from .models import PatternRow

    with db.session() as s:
        row = s.get(PatternRow, pattern_id)
        if row is not None:
            data = dict(row.data)
            data["hits"] = int(data.get("hits", 0)) + 1
            s.execute(update(PatternRow).where(PatternRow.id == pattern_id).values(data=data))


# ----------------------------------------------------------------------------- heavy LLM
def build_llm_request(ctx: Ctx) -> tuple[LLMRequest, list[Source], bool]:
    history: list[tuple[str, str]] = []
    if ctx.norm.context_dependent:
        budget = settings.history_max_tokens
        for turn in reversed(ctx.history):
            cost_ = count_tokens(turn["text"])
            if cost_ > budget:
                break
            history.insert(0, (turn["role"], turn["text"]))
            budget -= cost_
    blocks: list[ContextBlock] = []
    sources: list[Source] = []
    if ctx.knowledge is not None:
        from .rag.service import build_context

        blocks = build_context(ctx.knowledge, ctx.prompt)
        sources = [Source(n=b.n, document_id=b.document_id, title=b.title, page=b.page, chunk_id=b.chunk_id,
                          snippet=b.text[:280], collection_id=b.collection_id, version=b.version) for b in blocks]
    req = LLMRequest(question=ctx.prompt, context=blocks, history=history, knowledge=ctx.knowledge is not None)
    return req, sources, bool(blocks)


def _serve_llm(ctx: Ctx, info: LightInfoExtra) -> ChatResponse:
    _ensure_vector(ctx)
    t = time.perf_counter()
    req, sources, used_context = build_llm_request(ctx)
    if ctx.knowledge is not None:
        ctx.stages["context"] = _ms(t)
    t = time.perf_counter()
    res: LLMResult = heavy_model().complete(req)
    ctx.stages["llm"] = _ms(t)
    cited = _cited(res.text, sources)
    sources = [s for s in sources if s.n in cited]
    usd = cost(res.model_id, res.input_tokens, res.output_tokens, res.cache_read_tokens, res.cache_write_tokens)
    sp = res.split
    entry = LedgerEntry(request_id=ctx.request_id, user_id=ctx.user_id, route=Route.llm.value, ts=ctx.now,
                        model_id=res.model_id, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                        prompt_tokens=sp.get("prompt", 0), context_tokens=sp.get("context", 0),
                        history_tokens=sp.get("history", 0), system_tokens=sp.get("system", 0),
                        split_method=res.split_method, cache_read_tokens=res.cache_read_tokens,
                        cache_write_tokens=res.cache_write_tokens, embed_tokens=ctx.embed_tokens,
                        embed_cost_usd=_embed_usd(ctx),
                        cost_actual_usd=round(usd + _embed_usd(ctx), 10), simulated=ctx.simulated,
                        collection_ids=ctx.knowledge.collection_ids if ctx.knowledge else [],
                        context_fingerprint=ctx.knowledge.fingerprint if ctx.knowledge else None,
                        sources=[s.model_dump() for s in sources], uncited=used_context and not sources)
    # remember the answer (never for follow-ups; volatile only with a TTL)
    usage = {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens, **{f"{k}_tokens": v for k, v in sp.items()}}
    scope = GLOBAL if settings.global_cache and not contains_pii(ctx.prompt) else "user"
    light_cache().put(ctx.norm, ctx.prompt, ctx.vector, res.text, ctx.user_id, scope, usage, res.model_id,
                      fingerprint=ctx.knowledge.fingerprint if ctx.knowledge else None,
                      sources=[s.model_dump() for s in sources], now=ctx.now)
    light = _light(ctx, info.stage, prob=info.prob, margin=info.margin, reason=info.reason)
    tokens = TokenInfo(input_tokens=res.input_tokens, output_tokens=res.output_tokens, prompt_tokens=sp.get("prompt", 0),
                       context_tokens=sp.get("context", 0), history_tokens=sp.get("history", 0),
                       system_tokens=sp.get("system", 0), cache_read_tokens=res.cache_read_tokens,
                       cache_write_tokens=res.cache_write_tokens, cost_actual_usd=entry.cost_actual_usd)
    return _finish(ctx, entry, Route.llm, res.text, light, tokens, sources=sources, model_id=res.model_id,
                   artifact_fallback=info.fallback)


def _cited(text: str, sources: list[Source]) -> set[int]:
    import re

    nums = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return {s.n for s in sources if s.n in nums}


# ----------------------------------------------------------------------------- finish
def _finish(ctx: Ctx, entry: LedgerEntry, route_: Route, answer: str, light: LightInfo, tokens: TokenInfo,
            sources: list[Source] | None = None, confidence: float | None = None, pattern: Pattern | None = None,
            model_id: str | None = None, artifact_fallback: bool = False) -> ChatResponse:
    latency = round((time.perf_counter() - ctx.t0) * 1000, 2)
    entry.latency_ms = latency
    assert entry.estimate_method is None or entry.estimate_method in ESTIMATE_METHODS
    redacted = redact(ctx.prompt)
    expires = ctx.now + settings.log_retention_days * 86400
    # one transaction for everything the request writes: ledger entry, request log, both turns
    with db.session() as s:
        s.add(LedgerRow(**asdict(entry)))
        s.add(RequestLog(request_id=ctx.request_id, user_id=ctx.user_id, conversation_id=ctx.conversation_id,
                         ts=ctx.now, prompt=ctx.prompt, redacted=redacted, template=normalize(redacted).template,
                         template_key=ctx.norm.template_key, embedding=ctx.vector, route=route_.value,
                         pattern_id=pattern.id if pattern else None, answer=answer,
                         collection_ids=entry.collection_ids, knowledge=ctx.knowledge is not None,
                         light=light.model_dump(), latency_ms=latency, expires_at=expires))
        s.add(Turn(conversation_id=ctx.conversation_id, user_id=ctx.user_id, role="user", text=ctx.prompt,
                   request_id=ctx.request_id, ts=ctx.now, expires_at=expires))
        s.add(Turn(conversation_id=ctx.conversation_id, user_id=ctx.user_id, role="assistant", text=answer,
                   request_id=ctx.request_id, ts=ctx.now + 0.001, expires_at=expires))
    light_metrics().record(light.stage_hit, route_.value, light.latency_ms_by_stage,
                           recognized=light.stage_hit == "recognizer", artifact_fallback=artifact_fallback,
                           knowledge=ctx.knowledge is not None, skipped=light.stage_hit == "skipped")
    return ChatResponse(request_id=ctx.request_id, conversation_id=ctx.conversation_id, answer=answer, route=route_,
                        latency_ms=latency, confidence=confidence, pattern_id=pattern.id if pattern else None,
                        pattern_name=pattern.name if pattern else None,
                        artifact_kind=pattern.kind.value if pattern and pattern.kind else None,
                        model_id=model_id, tokens=tokens, light=light, sources=sources or [],
                        not_fully_deterministic=bool(pattern and pattern.not_fully_deterministic))
