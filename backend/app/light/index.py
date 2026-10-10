"""In-memory ANN index (hnswlib, cosine) keyed by string ids. Used by the semantic cache and
pattern kNN in v1; rebuilt from the database at startup and updated on write (§4.1)."""
from __future__ import annotations

import threading
from collections.abc import Sequence

import hnswlib
import numpy as np


class VectorIndex:
    def __init__(self, dim: int = 384, capacity: int = 1024) -> None:
        self.dim = dim
        self._lock = threading.Lock()
        self._index = hnswlib.Index(space="cosine", dim=dim)
        self._index.init_index(max_elements=capacity, ef_construction=100, M=16, random_seed=7)
        self._index.set_ef(64)
        self._labels: dict[str, int] = {}
        self._ids: dict[int, str] = {}
        self._next = 0

    def __len__(self) -> int:
        return len(self._labels)

    def add(self, key: str, vector: Sequence[float]) -> None:
        with self._lock:
            if key in self._labels:
                self._index.mark_deleted(self._labels.pop(key))
            if self._next >= self._index.get_max_elements():
                self._index.resize_index(self._index.get_max_elements() * 2)
            label = self._next
            self._next += 1
            self._index.add_items(np.asarray([vector], dtype=np.float32), [label])
            self._labels[key] = label
            self._ids[label] = key

    def remove(self, key: str) -> None:
        with self._lock:
            label = self._labels.pop(key, None)
            if label is not None:
                self._index.mark_deleted(label)
                self._ids.pop(label, None)

    def search(self, vector: Sequence[float], k: int = 5) -> list[tuple[str, float]]:
        """Nearest neighbours as (key, cosine similarity), best first."""
        with self._lock:
            n = len(self._labels)
            if n == 0:
                return []
            labels, dists = self._index.knn_query(np.asarray([vector], dtype=np.float32), k=min(k, n))
        return [(self._ids[int(lbl)], 1.0 - float(d)) for lbl, d in zip(labels[0], dists[0], strict=True)
                if int(lbl) in self._ids]
