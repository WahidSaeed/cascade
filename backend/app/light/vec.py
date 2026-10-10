"""Vector helpers (the cosine helper kept from the original embeddings.py)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

Vec = npt.NDArray[np.float32]


def as_vec(v: Sequence[float] | Vec) -> Vec:
    return np.asarray(v, dtype=np.float32)


def unit(v: Sequence[float] | Vec) -> Vec:
    a = as_vec(v)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def cosine(a: Sequence[float] | Vec, b: Sequence[float] | Vec) -> float:
    if len(a) == 0 or len(b) == 0 or len(a) != len(b):
        return 0.0
    va, vb = as_vec(a), as_vec(b)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def centroid(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    m = np.mean(np.asarray(vectors, dtype=np.float32), axis=0).astype(np.float32)
    return [float(x) for x in unit(m)]
