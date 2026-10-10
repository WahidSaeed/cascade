"""Artifact runner: executes ONE compiled artifact OUTSIDE the API process (BUILD.md rule 2).

Standalone on purpose (stdlib + optional numpy/onnxruntime/tokenizers, imported lazily, no `app`
imports), so the same file runs:
  - locally as a persistent subprocess:  python -I artifact_runner.py <artifact_dir>
    protocol: one JSON request per stdin line {"prompt": ...} -> one JSON line
              {"result": str, "confidence": float} | {"error": str}
  - in AWS Lambda, packaged as lambda_function.py: lambda_handler(event, context)

Artifact directory layout:
  manifest.json   {"kind": "rule"|"ml"|"dl", ...}
  handler.py      rule: defines handle(prompt) -> str | (str, confidence)
  model.onnx      ml / dl
  meta.json       ml / dl: classes, input mode, guards
  tokenizer.json  dl
"""
import json
import os
import re
import sys
from typing import Any

FALLBACK = "__FALLBACK__"
_QUOTED = re.compile(r"['\"‘’“”](.+)['\"‘’“”]\s*\??\s*$", re.S)


def payload_text(prompt: str, mode: str) -> str | None:
    if mode == "quoted":
        m = _QUOTED.search(prompt.strip())
        return m.group(1).strip() if m else None
    return prompt


class RuleArtifact:
    def __init__(self, path: str) -> None:
        with open(os.path.join(path, "handler.py")) as fh:
            code = fh.read()
        ns: dict[str, Any] = {"__name__": "artifact"}
        exec(compile(code, "handler.py", "exec"), ns)  # noqa: S102 - isolated process, never the API
        if "handle" not in ns:
            raise RuntimeError("handler defines no handle(prompt)")
        self._handle = ns["handle"]

    def run(self, prompt: str) -> tuple[str, float]:
        out = self._handle(prompt)
        if isinstance(out, tuple):
            return str(out[0]), float(out[1])
        return str(out), 1.0


def _softmax(x: Any) -> Any:
    import numpy as np

    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


class MlArtifact:
    """scikit-learn pipeline exported with skl2onnx (string tensor in, label + probabilities out)."""

    def __init__(self, path: str) -> None:
        import onnxruntime as ort

        with open(os.path.join(path, "meta.json")) as fh:
            self.meta = json.load(fh)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(os.path.join(path, "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name

    def run(self, prompt: str) -> tuple[str, float]:
        import numpy as np

        text = payload_text(prompt, self.meta.get("input", "quoted"))
        if not text or len(text.split()) < self.meta.get("min_words", 2):
            return FALLBACK, 0.0
        if not prompt.lower().startswith(tuple(self.meta.get("prefixes", [""]))):
            return FALLBACK, 0.0
        outs = self.sess.run(None, {self.input_name: np.array([[text]], dtype=object)})
        probs = np.asarray(outs[1], dtype=np.float32)[0]
        classes = self.meta["classes"]
        i = int(np.argmax(probs))
        return str(classes[i]), float(probs[i])


class DlArtifact:
    """Small transformer exported to ONNX; tokenized with the bundled tokenizer.json."""

    def __init__(self, path: str) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(os.path.join(path, "meta.json")) as fh:
            self.meta = json.load(fh)
        self.tok = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        self.max_len = int(self.meta.get("max_len", 64))
        self.tok.enable_truncation(self.max_len)
        self.tok.enable_padding(length=self.max_len)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(os.path.join(path, "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.vocab = set(self.meta.get("guard_vocab", []))

    def run(self, prompt: str) -> tuple[str, float]:
        import numpy as np

        text = payload_text(prompt, self.meta.get("input", "quoted"))
        if not prompt.lower().startswith(tuple(self.meta.get("prefixes", [""]))):
            return FALLBACK, 0.0
        # Input guard: too short, or no words the model saw in training -> defer to the LLM.
        words = re.findall(r"[a-z']+", (text or "").lower())
        if not text or len(words) < self.meta.get("min_words", 3):
            return FALLBACK, 0.0
        if self.vocab and sum(w in self.vocab for w in words) < max(2, len(words) // 3):
            return FALLBACK, 0.0
        enc = self.tok.encode(text)
        feed = {"input_ids": np.array([enc.ids], dtype=np.int64),
                "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
        logits = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
        probs = _softmax(np.asarray(logits, dtype=np.float32))[0]
        i = int(np.argmax(probs))
        return str(self.meta["classes"][i]), float(probs[i])


def load(path: str) -> Any:
    with open(os.path.join(path, "manifest.json")) as fh:
        manifest = json.load(fh)
    kind = manifest["kind"]
    if kind == "rule":
        return RuleArtifact(path)
    if kind == "ml":
        return MlArtifact(path)
    if kind == "dl":
        return DlArtifact(path)
    raise RuntimeError(f"unknown artifact kind {kind!r}")


def invoke(artifact: Any, prompt: str) -> dict[str, Any]:
    try:
        result, conf = artifact.run(prompt)
        return {"result": result, "confidence": conf}
    except Exception as exc:  # noqa: BLE001 - reported to the router, which falls back to the LLM
        return {"error": repr(exc)[:500]}


# ---------------------------------------------------------------- AWS Lambda entry point
_LAMBDA_ARTIFACT: Any = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # pragma: no cover - runs inside Lambda
    global _LAMBDA_ARTIFACT
    if _LAMBDA_ARTIFACT is None:
        _LAMBDA_ARTIFACT = load(os.environ.get("ARTIFACT_DIR", os.path.dirname(os.path.abspath(__file__))))
    return invoke(_LAMBDA_ARTIFACT, event.get("prompt", ""))


def main() -> None:
    path = sys.argv[1]
    try:
        artifact = load(path)
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(json.dumps({"fatal": repr(exc)[:500]}) + "\n")
        sys.stdout.flush()
        return
    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"error": "bad request"}
        else:
            resp = invoke(artifact, req.get("prompt", ""))
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
