"""Model files shared between tasks (ml / dl artifacts, agent candidates).

Training writes model files to the local disk of whichever task ran it (agent worker, API, seed), but approving,
verifying, retraining and rolling back can happen in another task. With CASCADE_ARTIFACT_BUCKET set, every model
directory under data_dir is mirrored to s3://<bucket>/models/<path relative to data_dir>: writers `publish` after
saving, readers `ensure_local` before reading, which downloads a directory this task has never seen. Without a
bucket (local dev, tests) both are no-ops and the local disk is the store.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import settings

PREFIX = "models/"


def _s3() -> Any:
    import boto3

    return boto3.client("s3", region_name=settings.bedrock_region)


def key_for(path: Path) -> str | None:
    """The S3 prefix mirroring `path`, or None when the path is not under data_dir."""
    try:
        rel = path.resolve().relative_to(Path(settings.data_dir).resolve()).as_posix()
    except ValueError:
        return None
    return PREFIX + rel.removeprefix(PREFIX) + "/"


def publish(path: Path, s3: Any = None) -> None:
    """Upload every file in the directory `path` (not recursive: model directories are flat)."""
    key = key_for(path)
    if not settings.artifact_bucket or key is None or not path.is_dir():
        return
    s3 = s3 or _s3()
    for f in sorted(path.iterdir()):
        if f.is_file():
            s3.put_object(Bucket=settings.artifact_bucket, Key=key + f.name, Body=f.read_bytes())


def ensure_local(path: Path, s3: Any = None) -> Path:
    """`path` with its files present: downloaded from the mirror if this task does not have them."""
    if path.is_dir() and any(f.is_file() for f in path.iterdir()):
        return path
    key = key_for(path)
    if not settings.artifact_bucket or key is None:
        return path
    s3 = s3 or _s3()
    objects = s3.list_objects_v2(Bucket=settings.artifact_bucket, Prefix=key).get("Contents", [])
    files = [o["Key"] for o in objects if "/" not in o["Key"][len(key):]]
    if files:
        path.mkdir(parents=True, exist_ok=True)
        for k in files:
            body = s3.get_object(Bucket=settings.artifact_bucket, Key=k)["Body"].read()
            (path / k[len(key):]).write_bytes(body)
    return path
