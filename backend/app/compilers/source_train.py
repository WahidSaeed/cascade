"""Training harness for user-edited `ml` / `dl` artifact code (the workspace editor's "Train").

Runs the edited file in a separate, resource-limited process (the local trainer) or a network-isolated SageMaker
training job; never in the API. Standalone: stdlib only, no `app` imports.

  python -I source_train.py --src <dir with handler.py, train.jsonl, job.json> --out <artifact dir> [--base <dir>]

The edited file must define train(prompts, labels[, base_dir]) and handle(prompt). train() writes the model files
next to the code (model.onnx, meta.json with "classes", tokenizer.json for dl); the harness then checks handle()
runs on a few training prompts, adds the job's metadata to meta.json and writes the manifest.
"""
import argparse
import importlib.util
import inspect
import json
import os
import shutil
import sys
import time
import traceback
import types
from typing import Any


def _load(path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("handler", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fn in ("train", "handle"):
        if not callable(getattr(mod, fn, None)):
            raise RuntimeError(f"the code must define {fn}()")
    return mod


def run(src: str, out: str, base: str) -> dict[str, Any]:
    t0 = time.time()
    os.makedirs(out, exist_ok=True)
    if os.path.abspath(src) != os.path.abspath(out):
        shutil.copy(os.path.join(src, "handler.py"), os.path.join(out, "handler.py"))
    with open(os.path.join(src, "job.json")) as fh:
        job = json.load(fh)
    rows = [json.loads(x) for x in open(os.path.join(src, "train.jsonl")) if x.strip()]
    prompts, labels = [r["prompt"] for r in rows], [str(r["label"]) for r in rows]
    mod = _load(os.path.join(out, "handler.py"))
    kwargs = {"base_dir": base} if base and "base_dir" in inspect.signature(mod.train).parameters else {}
    mod.train(prompts, labels, **kwargs)
    for f in ["model.onnx", "meta.json"] + (["tokenizer.json"] if job.get("kind") == "dl" else []):
        if not os.path.exists(os.path.join(out, f)):
            raise RuntimeError(f"train() did not write {f}")
    with open(os.path.join(out, "meta.json")) as fh:
        meta = json.load(fh)
    if not meta.get("classes"):
        raise RuntimeError('meta.json needs "classes" (the label order of the model outputs)')
    smoke = []
    for p in prompts[:5]:
        res = mod.handle(p)
        if not (isinstance(res, tuple) and len(res) == 2):
            raise RuntimeError("handle(prompt) must return (answer, confidence)")
        smoke.append(str(res[0]))
    meta.pop("integration", None)       # only an admin-approved integration (from the job) may be called at serve time
    meta.update({"kind": job.get("kind"), "source": True, "train_size": len(rows),
                 **({"integration": job["integration"]} if job.get("integration") else {})})
    # Score the evaluation prompts here, inside the sandbox: the verification gate reads these predictions and never
    # runs the edited code anywhere else before an admin approves it. (The model's own part: integrations aren't called.)
    eval_file = os.path.join(src, "eval.jsonl")
    if os.path.exists(eval_file):
        with open(eval_file) as fh, open(os.path.join(out, "predictions.jsonl"), "w") as pf:
            for line in fh:
                if not line.strip():
                    continue
                prompt = json.loads(line)["prompt"]
                t1 = time.perf_counter()
                try:
                    res = mod.handle(prompt)
                    pred = {"result": str(res[0]), "confidence": float(res[1]), "error": None}
                except Exception as exc:  # noqa: BLE001 - recorded as this prompt's error, like the runner does
                    pred = {"result": None, "confidence": 0.0, "error": repr(exc)[:300]}
                pred.update(prompt=prompt, latency_ms=round((time.perf_counter() - t1) * 1000, 3))
                pf.write(json.dumps(pred) + "\n")
    with open(os.path.join(out, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump({"kind": job.get("kind"), "source": True}, fh)
    return {"status": "Completed", "seconds": round(time.time() - t0, 1), "classes": meta["classes"],
            "smoke": smoke}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="")
    a = ap.parse_args()
    try:
        print(json.dumps(run(a.src, a.out, a.base)), flush=True)
    except Exception:  # noqa: BLE001 - the user's code: report the traceback in the job log
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
