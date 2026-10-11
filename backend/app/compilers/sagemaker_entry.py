"""SageMaker training entry point (`train` in the training image, infra/runtime-images/training.Dockerfile).

SageMaker mounts the `train` channel at /opt/ml/input/data/train, the job's hyperparameters at
/opt/ml/input/config/hyperparameters.json, and uploads /opt/ml/model as model.tar.gz when the process exits 0.

  mode=source   user-edited ml / dl code (handler.py, train.jsonl, job.json): source_train.py, network-isolated
  otherwise     the agent's dl fine-tune: dl_train_script.py with the job's hyperparameters
The base model is baked into the image (CASCADE_MODELS_DIR), so no job needs the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .dl import SCRIPT, SOURCE_SCRIPT, base_model_dir


def command(root: Path) -> list[str]:
    cfg = root / "input" / "config" / "hyperparameters.json"
    hp = {k: str(v) for k, v in json.loads(cfg.read_text()).items()} if cfg.exists() else {}
    data, out = root / "input" / "data" / "train", root / "model"
    if hp.get("mode") == "source":
        return [sys.executable, "-I", str(SOURCE_SCRIPT), "--src", str(data), "--out", str(out),
                "--base", base_model_dir() if hp.get("kind") == "dl" else ""]
    args = [sys.executable, str(SCRIPT), "--data", str(data / "train.jsonl"), "--base", base_model_dir(), "--out", str(out)]
    for k in ("epochs", "lr", "batch", "max_len", "seed", "prefixes"):
        if k in hp:
            args += [f"--{k}", hp[k]]
    return args


def main() -> None:
    root = Path(os.environ.get("SM_ROOT", "/opt/ml"))
    proc = subprocess.run(command(root), capture_output=True, text=True)  # noqa: S603 - our own scripts
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:                      # SageMaker shows /opt/ml/output/failure as the job's reason
        (root / "output").mkdir(parents=True, exist_ok=True)
        (root / "output" / "failure").write_text((proc.stdout + proc.stderr)[-1000:])
        sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
