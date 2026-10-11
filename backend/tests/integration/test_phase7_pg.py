"""Phase 7 on PostgreSQL + pgvector: the shared semantic cache and pattern kNN give the same matches as the
in-memory hnswlib path on the evaluation set (P7-01); rollups reconcile under the Postgres upserts and
FLOOR bucketing (P7-02); retrieval recall on the HNSW index holds the Phase 3 baseline (P7-05)."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app import db, engine
from app.config import settings
from app.light.cache import LightCache, light_cache
from app.light.embedder import get_embedder
from app.light.normalize import normalize
from app.light.recognizer import CentroidRecognizer
from app.models import CacheEntry
from app.store import store
from tests.eval.test_knowledge_quality import kb, test_retrieval_recall_at_5  # noqa: F401
from tests.helpers import install
from tests.unit.test_phase7_acceptance import (  # noqa: F401
    test_rebuild_reproduces_the_incremental_rollups,
    test_requests_routed_by_the_engine_land_in_the_rollups,
    test_rollups_give_exactly_the_raw_ledger_totals,
    test_rollups_reconcile_with_real_valued_costs,
    test_the_dashboard_reads_the_rollups,
)

pytestmark = pytest.mark.integration
EVAL = Path(settings.demo_dir) / "data" / "eval"


def load(name):
    return [json.loads(x) for x in (EVAL / f"{name}.jsonl").read_text().splitlines() if x.strip()]


@pytest.fixture
def routed(fresh_db):
    install("extraction")
    install("conversion")
    return store().approved_patterns()


@pytest.mark.acceptance("P7-01")
def test_pgvector_semantic_cache_matches_hnswlib_on_the_eval_set(routed):
    assert db.dialect_name() == "postgresql" and LightCache.shared_index()
    pairs = load("cache_pairs")
    open_ended = [r["prompt"] for r in load("router_must_not_match")][:60]
    users = ["ava", "ben"]
    for u in users:                                        # fill the cache through the real router
        for p in pairs:
            engine.route(p["first"], u)
        for q in open_ended[::2]:
            engine.route(q, u)
    queries = [p["second"] for p in pairs] + open_ended[1::2] + [p["first"] + " please" for p in pairs[:20]]
    emb = get_embedder()
    probes = []
    for q in queries:
        vec, _ = emb.embed(q)
        probes.append((normalize(q), vec))

    def answers(user):
        out = []
        for norm, vec in probes:
            hit = light_cache().lookup_semantic(norm, vec, user, None)
            out.append(hit.id if hit else None)
        return out

    shared = {u: answers(u) for u in users + ["nobody"]}
    settings.pgvector_index = False                        # the quick-start path over the same rows
    light_cache().reset()
    assert not LightCache.shared_index()
    in_memory = {u: answers(u) for u in users + ["nobody"]}
    assert shared == in_memory
    hits = sum(x is not None for x in shared["ava"])
    print(f"semantic cache: {hits} hits / {len(probes)} probes per user, identical on pgvector and hnswlib")
    assert hits >= 10
    with db.session() as s:                                 # never another user's private entries
        for user, ids in shared.items():
            for row in s.scalars(select(CacheEntry).where(CacheEntry.id.in_([i for i in ids if i]))):
                assert row.scope == "global" or row.user_id == user, (user, row.id)


@pytest.mark.acceptance("P7-01")
def test_pgvector_pattern_knn_matches_the_in_memory_recognizer(routed):
    shared, local = CentroidRecognizer(shared=True), CentroidRecognizer(shared=False)
    shared.refresh(routed)
    local.refresh(routed)
    prompts = [r["prompt"] for r in load("router_must_match")] + [r["prompt"] for r in load("router_must_not_match")]
    emb = get_embedder()
    matched = 0
    for q in prompts:
        norm = normalize(q)
        vec, _ = emb.embed(q)
        a, b = shared.recognize(norm, vec), local.recognize(norm, vec)
        assert (a.pattern_id, a.method, a.best_id) == (b.pattern_id, b.method, b.best_id), q
        assert a.prob == pytest.approx(b.prob, abs=1e-5) and a.margin == pytest.approx(b.margin, abs=1e-5), q
        matched += a.pattern_id is not None
    print(f"pattern kNN: {matched}/{len(prompts)} matched, identical on pgvector and in memory")
    assert matched >= 100


@pytest.mark.acceptance("P7-01")
def test_api_tasks_share_one_index(routed):
    """A second API task (a fresh process-local cache) sees entries the first one wrote: nothing is held in
    process memory on Postgres."""
    q = "Write a product description for the Ridgeline 2 tent"
    engine.route(q, "ava")
    light_cache().reset()                                  # as if served by another task
    assert not light_cache()._indexes and not light_cache()._exact
    again = engine.route("Please write a product description for the Ridgeline 2 tent", "ava")
    assert again.route.value == "cache" and again.light.stage_hit == "semantic_cache"
    assert engine.route(q, "ava").light.stage_hit == "exact_cache"


@pytest.mark.acceptance("P7-01")
def test_entries_for_other_values_never_crowd_out_the_match(fresh_db):
    """Twenty cached "describe the <N>-person tent" answers sit closer to the query than the right one could be
    ranked by distance alone; slots are filtered in SQL, so the entry with equal slots is still found."""
    from app.light.normalize import normalize as norm_

    emb = get_embedder()
    for n in range(1, 21):
        q = f"Write a product description for the {n} person tent in 120 words"
        vec, _ = emb.embed(q)
        light_cache().put(norm_(q), q, vec, f"answer {n}", "ava", "user", {}, "demo-llm")
    probe = "Please write a product description for the 7 person tent in 120 words"
    vec, _ = emb.embed(probe)
    hit = light_cache().lookup_semantic(norm_(probe), vec, "ava", None)
    assert hit is not None and hit.answer == "answer 7"


def test_services_starting_together_migrate_safely():
    """API tasks and the workers all migrate on start; concurrent upgrades of an empty database must not collide."""
    import os
    import threading
    import uuid

    from sqlalchemy import create_engine, inspect, text

    base = os.environ["CASCADE_TEST_DATABASE_URL"]
    name = f"cascade_t_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.rsplit("/", 1)[0] + f"/{name}"
    errors: list[BaseException] = []

    def run():
        try:
            db.migrate(url)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors, errors
        eng = create_engine(url)
        assert "ledger_rollup" in inspect(eng).get_table_names()
        eng.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
