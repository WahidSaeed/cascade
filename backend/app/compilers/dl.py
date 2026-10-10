"""`dl` artifacts (BUILD.md §4.3, §11.3): a small transformer fine-tuned through a *training job*.

  LocalTrainer      the build's stand-in: the same training script as a subprocess, with a tiny model
                    (`prajjwal1/bert-tiny`).
  SageMakerTrainer  AWS: CreateTrainingJob running the script, then the ONNX model is fetched from S3.

Both expose start() / status() / result(); the agent polls. Training time is recorded as overhead.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings

SCRIPT = Path(__file__).with_name("dl_train_script.py")


@dataclass
class DlJob:
    job_id: str
    out_dir: Path
    started: float
    trainer: str
    handle: Any = None


def base_model_dir() -> str:
    from huggingface_hub import snapshot_download

    return str(snapshot_download(settings.dl_base_model, cache_dir=settings.models_dir + "/hf"))


def write_dataset(prompts: list[str], labels: list[str], texts: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for t, lab in zip(texts, labels, strict=True):
            fh.write(json.dumps({"text": t, "label": lab}) + "\n")


class Trainer(ABC):
    @abstractmethod
    def start(self, data: Path, out_dir: Path, params: dict[str, Any]) -> DlJob: ...

    @abstractmethod
    def status(self, job: DlJob) -> str:
        """InProgress | Completed | Failed"""

    def fetch(self, job: DlJob) -> None:  # noqa: B027 - local jobs already wrote their output
        """Bring the trained model (model.onnx, tokenizer.json, meta.json) into job.out_dir."""


class LocalTrainer(Trainer):
    def start(self, data: Path, out_dir: Path, params: dict[str, Any]) -> DlJob:
        args = [sys.executable, str(SCRIPT), "--data", str(data), "--base", base_model_dir(), "--out", str(out_dir)]
        for k in ("epochs", "lr", "batch", "max_len", "seed", "prefixes"):
            if k in params:
                args += [f"--{k}", str(params[k])]
        log = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)  # noqa: SIM115 - kept for the job
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env={
            "PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "HF_HUB_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2"})
        return DlJob(job_id=f"local-{proc.pid}", out_dir=out_dir, started=time.time(), trainer="local",
                     handle=(proc, log.name))

    def status(self, job: DlJob) -> str:
        proc, _ = job.handle
        rc = proc.poll()
        if rc is None:
            return "InProgress"
        return "Completed" if rc == 0 and (job.out_dir / "model.onnx").exists() else "Failed"

    def log_tail(self, job: DlJob) -> str:
        return Path(job.handle[1]).read_text()[-2000:]


class SageMakerTrainer(Trainer):
    """CreateTrainingJob with the training script; the model is written to S3 and fetched on completion."""

    def __init__(self, client: Any = None, s3: Any = None) -> None:
        import boto3

        self._sm = client or boto3.client("sagemaker", region_name=settings.bedrock_region)
        self._s3 = s3 or boto3.client("s3", region_name=settings.bedrock_region)

    def start(self, data: Path, out_dir: Path, params: dict[str, Any]) -> DlJob:
        bucket = settings.artifact_bucket
        name = f"cascade-dl-{int(time.time())}"
        key = f"datasets/{name}/train.jsonl"
        self._s3.put_object(Bucket=bucket, Key=key, Body=data.read_bytes())
        self._sm.create_training_job(
            TrainingJobName=name,
            RoleArn=settings.sagemaker_role_arn,
            AlgorithmSpecification={"TrainingImage": settings.dl_training_image, "TrainingInputMode": "File"},
            HyperParameters={k: str(v) for k, v in params.items()},
            InputDataConfig=[{"ChannelName": "train", "DataSource": {"S3DataSource": {
                "S3DataType": "S3Prefix", "S3Uri": f"s3://{bucket}/datasets/{name}/", "S3DataDistributionType": "FullyReplicated"}}}],
            OutputDataConfig={"S3OutputPath": f"s3://{bucket}/models/"},
            ResourceConfig={"InstanceType": settings.dl_instance_type, "InstanceCount": 1, "VolumeSizeInGB": 10},
            StoppingCondition={"MaxRuntimeInSeconds": 3600},
        )
        return DlJob(job_id=name, out_dir=out_dir, started=time.time(), trainer="sagemaker")

    def status(self, job: DlJob) -> str:
        st = str(self._sm.describe_training_job(TrainingJobName=job.job_id)["TrainingJobStatus"])
        return "Failed" if st == "Stopped" else st

    def fetch(self, job: DlJob) -> None:
        """SageMaker writes model.tar.gz to S3; unpack it into the candidate directory."""
        import io
        import tarfile

        uri = str(self._sm.describe_training_job(TrainingJobName=job.job_id)["ModelArtifacts"]["S3ModelArtifacts"])
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        body = self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        job.out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            for m in tar.getmembers():
                name = Path(m.name).name
                if m.isfile() and name in ("model.onnx", "tokenizer.json", "meta.json", "manifest.json"):
                    f = tar.extractfile(m)
                    if f is not None:
                        (job.out_dir / name).write_bytes(f.read())


def trainer() -> Trainer:
    return SageMakerTrainer() if settings.dl_trainer == "sagemaker" else LocalTrainer()


def wait(t: Trainer, job: DlJob, timeout_s: float = 1800, poll_s: float = 1.0) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = t.status(job)
        if st != "InProgress":
            return st
        time.sleep(poll_s)
    return "Failed"


def training_cost(seconds: float) -> float:
    """Training compute is overhead: priced per instance-second (`_training` in model_prices)."""
    per_hour = settings.model_prices.get("_training", {}).get("per_hour", 0.0)
    return round(per_hour * seconds / 3600, 8)
