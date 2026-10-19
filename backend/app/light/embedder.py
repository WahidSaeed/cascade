"""Stage 3: the embedding model (BUILD.md §4.1). Loaded once per process.

Backends (CASCADE_EMBED_BACKEND):
  fastembed               BAAI/bge-small-en-v1.5 via ONNX on CPU (default, 0 tokens)
  multilingual-e5-small   intfloat/multilingual-e5-small via fastembed (non-English traffic)
  bedrock-titan           Titan Text Embeddings v2 (paid; token count recorded in the ledger)
  hashing                 deterministic bag-of-words (tests only)
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

from ..config import settings
from .vec import unit


class Embedder(ABC):
    model_id: str = "local-embedder"
    dim: int = 384
    paid: bool = False

    @abstractmethod
    def embed(self, text: str) -> tuple[list[float], int]:
        """Return (unit vector, token count)."""

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t)[0] for t in texts]


def _local_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


class FastEmbedEmbedder(Embedder):
    """Local ONNX embedder. fastembed downloads and locates the model files; inference runs on our own
    onnxruntime session so it can be tuned for the request path (no thread spin-waiting, which
    inflates p95 latency when the CPU is shared)."""

    def __init__(self, model_name: str | None = None) -> None:
        import onnxruntime as ort
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        self.model_id = model_name or settings.embed_model
        fe = TextEmbedding(self.model_id, cache_dir=settings.models_dir, threads=1)
        model_dir = Path(fe.model._model_dir)  # type: ignore[attr-defined]
        spec = next(m for m in TextEmbedding.list_supported_models() if m["model"] == self.model_id)
        del fe
        so = ort.SessionOptions()
        so.intra_op_num_threads = settings.embed_threads
        so.inter_op_num_threads = 1
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._sess = ort.InferenceSession(str(model_dir / spec["model_file"]), so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tok.no_padding()
        self._tok.enable_truncation(512)
        self._prefix = "query: " if "e5" in self.model_id else ""
        self._pooling = "mean" if "e5" in self.model_id else "cls"
        for _ in range(3):   # warm the session so the first request isn't slow
            self.dim = len(self.embed("warm up the light layer")[0])

    def _run(self, texts: list[str]) -> list[list[float]]:
        encs = self._tok.encode_batch([self._prefix + t for t in texts])
        width = max(len(e.ids) for e in encs)
        ids = np.zeros((len(encs), width), dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(encs):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = np.asarray(self._sess.run(None, feed)[0], dtype=np.float32)
        if self._pooling == "cls":
            pooled = hidden[:, 0]
        else:
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
        return [[float(x) for x in unit(v)] for v in pooled]

    def embed(self, text: str) -> tuple[list[float], int]:
        return self._run([text])[0], _local_tokens(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            out += self._run(texts[i:i + 32])
        return out


class TitanEmbedder(Embedder):
    paid = True

    def __init__(self) -> None:
        from ..heavy import bedrock_client

        self.model_id = settings.titan_embed_model_id
        self._client: Any = bedrock_client()
        self.dim = settings.embed_dim

    def embed(self, text: str) -> tuple[list[float], int]:
        body = json.dumps({"inputText": text, "dimensions": self.dim, "normalize": True})
        resp = self._client.invoke_model(modelId=self.model_id, body=body)
        payload = json.loads(resp["body"].read())
        return [float(x) for x in payload["embedding"]], int(payload.get("inputTextTokenCount", 0))


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder(Embedder):
    """Tests only: deterministic, not semantic."""

    model_id = "hashing"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, text: str) -> tuple[list[float], int]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in _WORD.findall(text.lower()):
            h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        return [float(x) for x in unit(vec)], _local_tokens(text)


_lock = threading.Lock()
_embedders: dict[str, Embedder] = {}


def get_embedder(backend: str | None = None) -> Embedder:
    name = backend or settings.embed_backend
    with _lock:
        if name not in _embedders:
            if name == "fastembed":
                _embedders[name] = FastEmbedEmbedder()
            elif name == "multilingual-e5-small":
                _embedders[name] = FastEmbedEmbedder("intfloat/multilingual-e5-small")
            elif name == "bedrock-titan":
                _embedders[name] = TitanEmbedder()
            elif name == "hashing":
                _embedders[name] = HashingEmbedder()
            else:
                raise ValueError(f"unknown embed backend {name!r}")
        return _embedders[name]
