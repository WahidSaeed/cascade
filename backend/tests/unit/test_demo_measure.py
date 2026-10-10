"""S10 (real numbers on Bedrock) — the build implements it and tests a dry run only."""
from app import demo_measure
from app.config import settings


def test_dry_run_estimates_and_never_calls_aws(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(demo_measure, "_replay", lambda *a, **k: called.append(1))
    out = tmp_path / "measured.md"
    r = demo_measure.run(5.0, dry_run=True, out=out)
    assert r["status"].startswith("dry run") and not called
    assert r["estimate"]["prompts"] == 200 and 0 < r["estimate"]["max_usd"] <= 5.0
    assert "Estimate (worst case)" in out.read_text()


def test_refuses_when_the_estimate_exceeds_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_measure, "_replay", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AWS")))
    r = demo_measure.run(0.0001, dry_run=False, out=tmp_path / "m.md")
    assert r["status"].startswith("refused")
    assert demo_measure.main(["--max-usd", "0.0001", "--dry-run"]) == 1
    assert settings.llm == "demo"
