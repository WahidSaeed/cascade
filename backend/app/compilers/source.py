"""Readable Python for `ml` / `dl` artifacts: what the agent trained and what the runner executes.

A trained artifact is an ONNX file, not code, so reviewers would otherwise see only its model card. This renders
the exact training recipe (the real hyperparameters, from the artifact's meta.json and the agent's params) and a
standalone `handle(prompt)` with the same guards as app/artifact_runner.py. Users edit it in the workspace editor;
edited code is trained by compilers/source_train.py in a sandbox and then served as written (SourceArtifact).
"""
from __future__ import annotations

from typing import Any

_PAYLOAD = '''_QUOTED = re.compile(r"['\\"\\u2018\\u2019\\u201c\\u201d](.+)['\\"\\u2018\\u2019\\u201c\\u201d]\\s*\\??\\s*$", re.S)


def payload_text(prompt: str) -> str | None:
    """The part of the prompt the model sees{doc}."""
{body}'''

_QUOTED_BODY = '''    m = _QUOTED.search(prompt.strip())
    return m.group(1).strip() if m else None
'''


def _payload(mode: str) -> str:
    if mode == "quoted":
        return _PAYLOAD.format(doc=": the quoted text at the end", body=_QUOTED_BODY)
    return _PAYLOAD.format(doc=": the whole prompt", body="    return prompt\n")


def _integration_note(meta: dict[str, Any]) -> str:
    spec = meta.get("integration")
    if not spec:
        return ""
    return (f"    # Feature source (approved integration): look up `{spec.get('field', 'value')}` at {spec.get('url', '')}\n"
            f"    # by the customer's {spec.get('param_from', 'email')} and compose {spec.get('format', '{prefix}{label}')!r};\n"
            "    # if the lookup fails, return FALLBACK so the LLM answers.\n")


def _header(kind: str, name: str, version: int, train_size: int, what: str) -> str:
    return (f'"""{name} — `{kind}` artifact v{version}.\n\n'
            f"{what}\nTrained on {train_size} labelled examples. train() is the exact recipe; handle() is what serves each\n"
            "request (out of process, next to model.onnx). Edit either and press Train: the code runs as a sandboxed\n"
            'training job and the result is evaluated against the LLM before anyone can approve it.\n"""\n')


def ml_source(meta: dict[str, Any], params: dict[str, Any], name: str, version: int, train_size: int) -> str:
    ngram = int(params.get("ngram_max", 1))
    min_df = int(params.get("min_df", 1))
    sublinear = bool(params.get("sublinear_tf", True))
    if meta.get("model", params.get("model")) == "gboost":
        clf_import = "from sklearn.ensemble import GradientBoostingClassifier"
        clf = "GradientBoostingClassifier(random_state=7)"
        what = "TF-IDF features + gradient boosting (scikit-learn), exported to ONNX with skl2onnx."
    else:
        clf_import = "from sklearn.linear_model import LogisticRegression"
        clf = (f"LogisticRegression(C={float(meta.get('C', params.get('C', 1.0)))!r}, "
               f"class_weight={meta.get('class_weight', params.get('class_weight'))!r}, max_iter=3000, random_state=7)")
        what = "TF-IDF features + logistic regression (scikit-learn), exported to ONNX with skl2onnx."
    mode = meta.get("input", "quoted")
    return f'''{_header("ml", name, version, train_size, what)}import json
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))  # the artifact directory: model.onnx, meta.json
PREFIXES = {tuple(meta.get("prefixes", [""]))!r}   # the prompt shapes this pattern was trained on
MIN_WORDS = {int(meta.get("min_words", 2))}
FALLBACK = "__FALLBACK__"                          # "not mine": the router sends the prompt to the LLM

{_payload(mode)}

# ---------------------------------------------------------------- training (what the agent ran)
def train(prompts: list[str], labels: list[str]):
    from skl2onnx import to_onnx
    from skl2onnx.common.data_types import StringTensorType
    {clf_import}
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import Pipeline

    X = [(payload_text(p) or p).lower() for p in prompts]
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, {ngram}), min_df={min_df}, sublinear_tf={sublinear},
                                  lowercase=False, token_pattern=r"(?u)\\b\\w\\w+\\b")),
        ("clf", {clf}),
    ])
    pipe.fit(X, labels)
    clf = pipe.named_steps["clf"]
    onx = to_onnx(pipe, initial_types=[("input", StringTensorType([None, 1]))],
                  options={{id(clf): {{"zipmap": False}}}}, target_opset={{"": 17, "ai.onnx.ml": 3}})
    with open(os.path.join(HERE, "model.onnx"), "wb") as fh:
        fh.write(onx.SerializeToString())
    with open(os.path.join(HERE, "meta.json"), "w") as fh:
        json.dump({{"classes": [str(c) for c in pipe.classes_]}}, fh)
    return pipe


# ---------------------------------------------------------------- inference (what the runner executes)
_state: dict = {{}}


def handle(prompt: str) -> tuple[str, float]:
    import onnxruntime as ort

    text = payload_text(prompt)
    if not text or len(text.split()) < MIN_WORDS or not prompt.lower().startswith(PREFIXES):
        return FALLBACK, 0.0
    if not _state:
        with open(os.path.join(HERE, "meta.json")) as fh:
            _state["classes"] = json.load(fh)["classes"]
        _state["sess"] = ort.InferenceSession(os.path.join(HERE, "model.onnx"), providers=["CPUExecutionProvider"])
    probs = _state["sess"].run(None, {{"input": np.array([[text.lower()]], dtype=object)}})[1][0]
    i = int(np.argmax(probs))
{_integration_note(meta)}    return _state["classes"][i], float(probs[i])
'''


def dl_source(meta: dict[str, Any], params: dict[str, Any], name: str, version: int, train_size: int) -> str:
    base = params.get("base_model") or meta.get("base_model") or "prajjwal1/bert-tiny"   # the model id, not a cache hash
    epochs = int(meta.get("epochs", params.get("epochs", 6)))
    lr = float(meta.get("lr", params.get("lr", 5e-4)))
    batch = int(params.get("batch", 32))
    max_len = int(meta.get("max_len", 48))
    what = (f"A small transformer ({base}) fine-tuned for sequence classification with PyTorch +\n"
            "Hugging Face transformers, exported to ONNX.")
    loss = f" Final training loss {meta['final_loss']}." if meta.get("final_loss") is not None else ""
    return f'''{_header("dl", name, version, train_size, what + loss)}import collections
import json
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))  # the artifact directory: model.onnx, tokenizer.json, meta.json
PREFIXES = {tuple(meta.get("prefixes", [""]))!r}   # the prompt shapes this pattern was trained on
MIN_WORDS = {int(meta.get("min_words", 3))}
MAX_LEN = {max_len}
FALLBACK = "__FALLBACK__"                          # "not mine": the router sends the prompt to the LLM

{_payload(meta.get("input", "quoted"))}

# ---------------------------------------------------------------- training (what the training job ran)
def train(prompts: list[str], labels: list[str], base_dir: str = "{base}"):
    import torch
    from tokenizers import BertWordPieceTokenizer
    from transformers import BertConfig, BertForSequenceClassification

    torch.manual_seed(7)
    texts = [payload_text(p) or p for p in prompts]
    classes = sorted(set(labels))
    index = {{c: i for i, c in enumerate(classes)}}
    tok = BertWordPieceTokenizer(f"{{base_dir}}/vocab.txt", lowercase=True)
    tok.enable_truncation(MAX_LEN)
    tok.enable_padding(length=MAX_LEN)
    enc = tok.encode_batch(texts)
    ids = torch.tensor([e.ids for e in enc])
    mask = torch.tensor([e.attention_mask for e in enc])
    types = torch.tensor([e.type_ids for e in enc])
    y = torch.tensor([index[label] for label in labels])

    model = BertForSequenceClassification.from_pretrained(
        base_dir, config=BertConfig.from_pretrained(base_dir, num_labels=len(classes)))
    opt = torch.optim.AdamW(model.parameters(), lr={lr!r})
    model.train()
    for _ in range({epochs}):                               # epochs
        perm = torch.randperm(len(texts))
        for i in range(0, len(texts), {batch}):             # batch size
            b = perm[i:i + {batch}]
            out = model(input_ids=ids[b], attention_mask=mask[b], token_type_ids=types[b], labels=y[b])
            opt.zero_grad()
            out.loss.backward()
            opt.step()
    model.eval()
    torch.onnx.export(model, (ids[:1], mask[:1], types[:1]), os.path.join(HERE, "model.onnx"),
                      input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["logits"],
                      dynamic_axes={{k: {{0: "batch"}} for k in ("input_ids", "attention_mask", "token_type_ids", "logits")}},
                      opset_version=17, dynamo=False)
    tok.save(os.path.join(HERE, "tokenizer.json"))
    words = collections.Counter(w for t in texts for w in re.findall(r"[a-z']+", t.lower()))
    with open(os.path.join(HERE, "meta.json"), "w") as fh:     # the input guard's vocabulary: words seen twice+
        json.dump({{"classes": classes, "guard_vocab": [w for w, c in words.items() if c >= 2]}}, fh)
    return model


# ---------------------------------------------------------------- inference (what the runner executes)
_state: dict = {{}}


def handle(prompt: str) -> tuple[str, float]:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    if not _state:
        tok = Tokenizer.from_file(os.path.join(HERE, "tokenizer.json"))
        tok.enable_truncation(MAX_LEN)
        tok.enable_padding(length=MAX_LEN)
        with open(os.path.join(HERE, "meta.json")) as fh:
            meta = json.load(fh)
        _state.update(tok=tok, classes=meta["classes"], vocab=set(meta.get("guard_vocab", [])),
                      sess=ort.InferenceSession(os.path.join(HERE, "model.onnx"), providers=["CPUExecutionProvider"]))
    text = payload_text(prompt)
    if not prompt.lower().startswith(PREFIXES):
        return FALLBACK, 0.0
    # Input guard: too short, or too few words the model saw in training -> defer to the LLM.
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not text or len(words) < MIN_WORDS:
        return FALLBACK, 0.0
    if _state["vocab"] and sum(w in _state["vocab"] for w in words) < max(2, len(words) // 3):
        return FALLBACK, 0.0
    enc = _state["tok"].encode(text)
    feed = {{"input_ids": np.array([enc.ids], dtype=np.int64),
             "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
             "token_type_ids": np.array([enc.type_ids], dtype=np.int64)}}
    logits = _state["sess"].run(None, feed)[0][0]
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    i = int(np.argmax(probs))
    return _state["classes"][i], float(probs[i])
'''


def artifact_source(kind: str, meta: dict[str, Any], params: dict[str, Any] | None, name: str, version: int,
                    train_size: int) -> str:
    """Python for an `ml` / `dl` artifact; "" for other kinds (rules carry their own handler_code)."""
    if kind == "ml":
        return ml_source(meta, params or {}, name, version, train_size)
    if kind == "dl":
        return dl_source(meta, params or {}, name, version, train_size)
    return ""
