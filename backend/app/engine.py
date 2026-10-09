"""The router. Decides the cheapest correct path for each request and logs the outcome."""
from __future__ import annotations

import time

from .config import settings
from .embeddings import cosine, embed
from .heavy import heavy_model
from .sandbox import HandlerError, run_handler
from .schemas import ChatResponse, Route
from .store import store


def _match_pattern(embedding: list[float]):
    best = None
    for p in store().approved_patterns():
        sim = cosine(embedding, p.embedding)
        if best is None or sim > best[1]:
            best = (p, sim)
    if best and best[1] >= settings.pattern_threshold:
        return best
    return None


def route(prompt: str) -> ChatResponse:
    t0 = time.perf_counter()
    emb = embed(prompt)
    st = store()

    # A. semantic cache
    hit = st.cache_lookup(emb)
    if hit and hit[1] >= settings.cache_threshold:
        latency = (time.perf_counter() - t0) * 1000
        st.log_request(prompt, emb, Route.cache.value, latency)
        return ChatResponse(answer=hit[0], route=Route.cache, latency_ms=round(latency, 2),
                            confidence=round(hit[1], 4))

    # B. deterministic fast path
    match = _match_pattern(emb)
    if match:
        pattern, sim = match
        try:
            out = run_handler(pattern.id, pattern.handler_code, prompt)
            if out.strip().lower() != "__fallback__":
                pattern.hits += 1
                st.put_pattern(pattern)
                latency = (time.perf_counter() - t0) * 1000
                st.log_request(prompt, emb, Route.fast_path.value, latency)
                return ChatResponse(
                    answer=out, route=Route.fast_path, latency_ms=round(latency, 2),
                    confidence=round(sim, 4), pattern_id=pattern.id, pattern_name=pattern.name,
                )
        except HandlerError:
            pass  # fall through to heavy model on any handler failure

    # C. heavy model
    answer = heavy_model().generate(prompt)
    latency = (time.perf_counter() - t0) * 1000
    st.cache_put(prompt, emb, answer)          # remember for next time
    st.log_request(prompt, emb, Route.heavy.value, latency)
    return ChatResponse(answer=answer, route=Route.heavy, latency_ms=round(latency, 2))
