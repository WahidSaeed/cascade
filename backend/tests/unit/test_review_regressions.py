"""Regressions from the Phase 1 acceptance review."""
import pytest
from sqlalchemy import func, select

from app import db, engine
from app import patterns as pat
from app.config import settings
from app.ledger import LedgerEntry, ledger
from app.models import CacheEntry
from app.schemas import CompileRequest, PatternEdit
from tests.helpers import examples, install


@pytest.mark.acceptance("P1-01")
def test_swapped_units_never_share_a_cached_answer(fresh_db):
    a = engine.route("Convert 5 km to miles", "ava")
    b = engine.route("convert 5 miles to km", "ava")
    assert a.answer == "5 km = 3.11 miles" and b.route.value == "llm" and b.answer == "5 miles = 8.05 km"
    c = engine.route("Convert 5 kg to lb", "ava")
    d = engine.route("Convert 5 lb to kg", "ava")
    assert d.route.value == "llm" and d.answer != c.answer
    e = engine.route("Translate good morning from English to French", "ben")
    f = engine.route("Translate good morning from French to English", "ben")
    assert e.route.value == f.route.value == "llm"


def test_cached_artifact_answers_die_with_the_artifact(fresh_db):
    p = install("conversion")
    first = engine.route("Convert 7 kg to lb", "ava")
    assert first.route.value == "artifact"
    assert engine.route("Convert 7 kg to lb", "ava").route.value == "cache"
    pat.reject(p.id)
    again = engine.route("Convert 7 kg to lb", "ava")
    assert again.route.value == "llm"                      # neither the artifact nor its cached answers serve
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(CacheEntry).where(CacheEntry.pattern_id == p.id)) == 0

    q = install("extraction")
    prompt = "Extract the order number, email and amount: 'Order NP-11111 for li@example.com cost €9.50'"
    assert engine.route(prompt, "ben").route.value == "artifact"
    pat.demote(q.id, "drift")
    assert engine.route(prompt, "ben").route.value == "llm"


@pytest.mark.acceptance("P1-02")
def test_low_confidence_artifact_falls_back_to_llm(fresh_db, monkeypatch):
    """S2: an ambiguous input gets low confidence from the artifact and goes to the LLM."""
    code = ("def handle(prompt):\n"
            "    t = prompt.lower()\n"
            "    if 'km' not in t:\n"
            "        return '__FALLBACK__'\n"
            "    return ('unsure', 0.3) if 'about' in t else ('sure', 0.95)\n")
    p = pat.compile_pattern(CompileRequest(name="probe", description="probe", task_type="transform",
                                           examples=examples("conversion"), handler_code=code))
    p.verification = pat.VerifyResult(pattern_id=p.id, score=1.0, sample_size=10, passed=True)
    p.verification_score = 1.0
    from app.store import store

    store().put_pattern(p)
    pat.approve(p.id, "eli")
    assert settings.artifact_min_confidence == 0.6
    monkeypatch.setattr(settings, "global_cache", False)       # isolate the artifact path from the cache
    sure = engine.route("Convert 12 km to miles", "ava")
    unsure = engine.route("Convert about 12 km to miles", "ben")
    assert sure.route.value == "artifact" and sure.confidence == pytest.approx(0.95)
    assert unsure.route.value == "llm" and "confidence 0.30" in unsure.light.reason
    assert unsure.answer == "12 km = 7.46 miles"


def test_net_savings_subtract_embedding_cost(fresh_db):
    ledger().record(LedgerEntry(request_id="r1", user_id="u", route="cache", cost_avoided_usd=0.01,
                                embed_tokens=100, embed_cost_usd=0.002, cost_actual_usd=0.002,
                                estimate_method="cache_exact"))
    s = ledger().summary()
    assert s["net_saved_usd"] == pytest.approx(0.008) and s["embed_cost_usd"] == pytest.approx(0.002)
    assert ledger().by_user()[0]["net_saved_usd"] == pytest.approx(0.008)
    assert sum(b["net_saved_usd"] for b in ledger().timeseries()) == pytest.approx(0.008)


def test_metadata_edit_keeps_artifact_serving(fresh_db):
    p = install("conversion")
    pat.edit(p.id, PatternEdit(name="Units"))
    assert engine.route("Convert 3 kg to lb", "ava").route.value == "artifact"
