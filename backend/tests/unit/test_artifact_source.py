"""Readable Python for ml / dl artifacts: it must describe exactly what was trained and what is served."""
import random
from dataclasses import asdict

from app import artifact_runner
from app import demo_world as w
from app.compilers.ml import MlParams, train_ml
from app.compilers.source import artifact_source


def _load(code: str, file: str) -> dict:
    ns: dict = {"__name__": "artifact_source", "__file__": file}
    exec(compile(code, "source.py", "exec"), ns)  # noqa: S102 - the test process, never the API
    return ns


def _triage(n: int) -> tuple[list[str], list[str]]:
    rng = random.Random(5)
    rows = [w.make_triage_prompt(rng, w.TRIAGE_CLASSES[i % 6]) for i in range(n)]
    return [r[0] for r in rows], [w.keyword_rule(r[1]) for r in rows]


PROBES = ["Classify this support ticket: 'I was charged twice for my tent order, please fix the invoice'",
          "Classify this support ticket: 'My parcel tracking hasn't moved in a week'",
          "Classify this support ticket: 'hi'",                       # too short -> fallback
          "Write me a poem about 'mountains and rivers at dawn'"]     # another prompt shape -> fallback


def test_ml_source_reproduces_training_and_serving(tmp_path):
    prompts, labels = _triage(120)
    params = MlParams(ngram_max=2, C=4.0, class_weight="balanced")
    model_dir = tmp_path / "artifact"
    trained = train_ml(prompts, labels, model_dir, params, prefixes=["classify this support ticket"])
    code = artifact_source("ml", trained.meta, {"features": "tfidf", **asdict(params)}, "Ticket triage", 2, 120)
    assert "LogisticRegression(C=4.0, class_weight='balanced'" in code and "ngram_range=(1, 2)" in code

    served = artifact_runner.MlArtifact(str(model_dir))
    ns = _load(code, str(model_dir / "handler.py"))
    for q in PROBES:
        got, conf = ns["handle"](q)
        want, want_conf = served.run(q)
        assert got == want and abs(conf - want_conf) < 1e-5, q
    assert ns["handle"](PROBES[2])[0] == artifact_runner.FALLBACK

    # train() is the real recipe: retraining from the shown code gives the same predictions
    retrain = tmp_path / "retrain"
    retrain.mkdir()
    ns2 = _load(code, str(retrain / "handler.py"))
    ns2["train"](prompts, labels)
    for q in PROBES:
        assert ns2["handle"](q)[0] == served.run(q)[0], q


def test_rules_carry_their_own_code():
    assert artifact_source("rule", {}, {}, "x", 1, 0) == ""


def test_dl_source_is_valid_python_with_the_real_settings():
    meta = {"classes": ["1", "2", "3", "4", "5"], "max_len": 48, "input": "quoted", "min_words": 3,
            "prefixes": ["rate the urgency"], "guard_vocab": ["boots", "trek"], "base_model": "bert-tiny",
            "epochs": 8, "lr": 0.0005, "final_loss": 0.12}
    code = artifact_source("dl", meta, {"base_model": "bert-tiny"}, "Urgency scoring", 3, 400)
    ns = _load(code, "/tmp/x/handler.py")
    assert ns["MAX_LEN"] == 48 and ns["PREFIXES"] == ("rate the urgency",)
    assert "range(8)" in code and "lr=0.0005" in code and "Final training loss 0.12" in code
    assert ns["payload_text"]("Rate the urgency 1-5: 'boots late'") == "boots late"

