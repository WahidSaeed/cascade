"""Embeddings for the router. Local deterministic embedder for offline dev; Bedrock for prod.

The local embedder is a hashing bag-of-words projected to a fixed dimension. It is NOT
semantically strong, but it is deterministic and dependency-free, so cache/pattern matching
works offline and in tests. Swap EMBED_BACKEND=bedrock for real embeddings.
"""
from __future__ import annotations

import hashlib
import math
import re

import numpy as np

from .config import settings

_WORD = re.compile(r"[a-z0-9]+")


def _local_embed(text: str) -> list[float]:
    vec = np.zeros(settings.embed_dim, dtype=np.float32)
    tokens = _WORD.findall(text.lower())
    if not tokens:
        return vec.tolist()
    for tok in tokens:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % settings.embed_dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _bedrock_embed(text: str) -> list[float]:
    import json
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
    body = json.dumps({"inputText": text})
    resp = client.invoke_model(modelId=settings.bedrock_embed_model_id, body=body)
    payload = json.loads(resp["body"].read())
    return payload["embedding"]


def embed(text: str) -> list[float]:
    if settings.embed_backend == "bedrock":
        return _bedrock_embed(text)
    return _local_embed(text)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
