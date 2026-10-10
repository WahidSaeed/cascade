"""`ml` artifacts (BUILD.md §4.3, §11.3): TF-IDF features + logistic regression (or gradient boosting),
exported with skl2onnx (string tensor in, label + calibrated probabilities out) and served out of
process by app/artifact_runner.py (locally) or the `ml-runtime` Lambda image (AWS).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_QUOTED = re.compile(r"['\"\u2018\u2019\u201c\u201d](.+)['\"\u2018\u2019\u201c\u201d]\s*\??\s*$", re.S)


def payload_text(prompt: str, mode: str) -> str | None:
    """The part of the prompt the model sees (same rule as the runtime's)."""
    if mode == "quoted":
        m = _QUOTED.search(prompt.strip())
        return m.group(1).strip() if m else None
    return prompt


@dataclass
class MlParams:
    ngram_max: int = 1                    # 1 = unigrams, 2 = + word bigrams
    C: float = 1.0
    class_weight: str | None = None       # None | "balanced"
    model: str = "logreg"                 # logreg | gboost
    min_df: int = 1
    sublinear_tf: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrainedModel:
    path: Path
    classes: list[str]
    params: MlParams
    train_size: int
    seconds: float
    size_mb: float
    meta: dict[str, Any]


def texts_for(prompts: list[str], input_mode: str) -> list[str]:
    return [payload_text(p, input_mode) or p for p in prompts]


def train_ml(prompts: list[str], labels: list[str], out_dir: Path, params: MlParams, input_mode: str = "quoted",
             prefixes: list[str] | None = None) -> TrainedModel:
    from skl2onnx import to_onnx
    from skl2onnx.common.data_types import StringTensorType
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    t0 = time.time()
    X = texts_for(prompts, input_mode)
    vec = TfidfVectorizer(ngram_range=(1, params.ngram_max), min_df=params.min_df, sublinear_tf=params.sublinear_tf,
                          lowercase=True, token_pattern=r"(?u)\b\w\w+\b")  # noqa: S106 - a regex, not a password
    clf: Any
    if params.model == "gboost":
        clf = GradientBoostingClassifier(random_state=7)
    else:
        clf = LogisticRegression(C=params.C, class_weight=params.class_weight, max_iter=3000, random_state=7)
    pipe = Pipeline([("tfidf", vec), ("clf", clf)])
    pipe.fit(X, labels)
    onx = to_onnx(pipe, initial_types=[("input", StringTensorType([None, 1]))],
                  options={id(clf): {"zipmap": False}}, target_opset={"": 17, "ai.onnx.ml": 3})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "model.onnx").write_bytes(onx.SerializeToString())
    classes = [str(c) for c in pipe.classes_]
    meta = {"kind": "ml", "classes": classes, "input": input_mode, "min_words": 2,
            "prefixes": [p.lower() for p in (prefixes or [""])], "features": f"tfidf 1-{params.ngram_max}gram",
            "model": params.model, "C": params.C, "class_weight": params.class_weight}
    (out_dir / "meta.json").write_text(json.dumps(meta))
    (out_dir / "manifest.json").write_text(json.dumps({"kind": "ml"}))
    size = (out_dir / "model.onnx").stat().st_size / 1e6
    return TrainedModel(out_dir, classes, params, len(X), round(time.time() - t0, 2), round(size, 3), meta)
