"""Blob storage for documents, datasets and modules: the local filesystem in development, S3 in AWS
(CASCADE_BLOB_BUCKET). Uploads go through presigned URLs (S3) or the API's upload endpoint (local)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Any

from .config import settings

_SECRET = secrets.token_bytes(32)


def _local_path(key: str) -> Path:
    root = (Path(settings.data_dir) / "blobs").resolve()
    p = (root / key).resolve()
    if root not in p.parents:
        raise ValueError("invalid blob key")
    return p


def _s3() -> Any:
    import boto3

    return boto3.client("s3", region_name=settings.bedrock_region)


def put(key: str, data: bytes) -> str:
    if settings.blob_bucket:
        _s3().put_object(Bucket=settings.blob_bucket, Key=key, Body=data)
        return f"s3://{settings.blob_bucket}/{key}"
    p = _local_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return f"file://{key}"


def get(key: str) -> bytes:
    if settings.blob_bucket:
        return bytes(_s3().get_object(Bucket=settings.blob_bucket, Key=key)["Body"].read())
    return _local_path(key).read_bytes()


def exists(key: str) -> bool:
    if settings.blob_bucket:
        try:
            _s3().head_object(Bucket=settings.blob_bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False
    return _local_path(key).exists()


def upload_url(key: str, ttl_s: int = 900) -> dict[str, Any]:
    """Where the client should PUT the file: an S3 presigned URL, or the local API endpoint."""
    if settings.blob_bucket:
        url = _s3().generate_presigned_url("put_object", Params={"Bucket": settings.blob_bucket, "Key": key},
                                           ExpiresIn=ttl_s)
        return {"url": url, "method": "PUT", "key": key}
    exp = int(time.time()) + ttl_s
    return {"url": f"/api/uploads?key={key}&exp={exp}&sig={sign(key, exp)}", "method": "PUT", "key": key}


def sign(key: str, exp: int) -> str:
    return hmac.new(_SECRET, f"{key}|{exp}".encode(), hashlib.sha256).hexdigest()


def check_signature(key: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(sign(key, exp), sig)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
