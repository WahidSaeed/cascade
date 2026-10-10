"""Light-layer latency (BUILD.md §4.1, P1-02): p95 of normalize + exact cache + embed + semantic cache +
recognizer under 25 ms on the dev machine. Measured here, without coverage instrumentation, after a
20-request warm-up. Phase 7 extends this to 1,000 prompts and knowledge-scoped prompts (P7-04)."""
import random
import statistics

import pytest

from app import demo_world as w
from app import engine
from tests.helpers import install

LIGHT_STAGES = ("normalize", "exact_cache", "embed", "semantic_cache", "recognizer")


def light_ms(resp) -> float:
    st = resp.light.latency_ms_by_stage
    return sum(st.get(k, 0.0) for k in LIGHT_STAGES)


@pytest.mark.acceptance("P1-02")
def test_light_layer_p95_under_25ms(fresh_db):
    install("extraction")
    rng = random.Random(99)
    prompts = [w.make_open_ended(rng) + f" #{i}" for i in range(130)] + [w.make_conversion(rng) for _ in range(130)]
    rng.shuffle(prompts)
    samples = [light_ms(engine.route(q, "ben")) for q in prompts][20:]
    p95 = statistics.quantiles(samples, n=20)[18]
    print(f"light layer p50 {statistics.median(samples):.1f} ms, p95 {p95:.1f} ms over {len(samples)} requests")
    assert p95 < 25.0, f"light p95 {p95:.1f} ms"
