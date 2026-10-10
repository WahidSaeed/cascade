"""User datasets for agent runs (BUILD.md §11.2 question 2).

On registration: schema validation (the mapped columns exist), row count, label distribution, PII scan
(redact or reject), and de-duplication against logged prompts. Rows are stored as JSONL in the blob store.
User labels count as ground truth and take precedence in evaluation.
"""
from __future__ import annotations

import csv
import io
import json
import time
from collections import Counter
from typing import Any

from sqlalchemy import select

from .. import blobstore, db
from ..light.normalize import clean
from ..models import DatasetRow, RequestLog, new_id
from ..privacy import contains_pii, redact


class DatasetError(ValueError):
    pass


def _read_rows(data: bytes, filename: str) -> list[dict[str, Any]]:
    name = filename.lower()
    text = data.decode("utf-8-sig", "replace")
    if name.endswith((".jsonl", ".ndjson")):
        return [json.loads(x) for x in text.splitlines() if x.strip()]
    if name.endswith(".parquet"):
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DatasetError("Parquet needs pyarrow (pip install pyarrow); upload CSV or JSONL instead") from exc
        return list(pq.read_table(io.BytesIO(data)).to_pylist())
    return list(csv.DictReader(io.StringIO(text)))


def _payload(prompt: str) -> str:
    from ..compilers.ml import payload_text

    return clean(payload_text(prompt, "quoted") or prompt).lower()


def register(owner: str, filename: str, columns_map: dict[str, str], use: str, data: bytes | None = None,
             key: str | None = None, pii_action: str = "redact") -> dict[str, Any]:
    if use not in ("train", "eval", "both"):
        raise DatasetError("use must be train, eval or both")
    if data is None:
        if not key or not blobstore.exists(key):
            raise DatasetError("upload not found")
        data = blobstore.get(key)
    rows = _read_rows(data, filename)
    if not rows:
        raise DatasetError("the file has no rows")
    in_col = columns_map.get("input")
    label_col = columns_map.get("label") or columns_map.get("expected_output")
    cols = set(rows[0])
    missing = [c for c in (in_col, label_col) if c and c not in cols]
    if not in_col or missing:
        raise DatasetError(f"column mapping does not match the file (columns: {sorted(cols)}; missing: {missing})")
    clean_rows: list[dict[str, str]] = []
    pii_rows = 0
    within_dupes = 0
    seen: set[str] = set()
    for r in rows:
        text = str(r.get(in_col) or "").strip()
        if not text:
            continue
        norm = clean(text).lower()
        if norm in seen:                       # duplicate row within the file: kept once
            within_dupes += 1
            continue
        seen.add(norm)
        if contains_pii(text):
            pii_rows += 1
            if pii_action == "reject":
                raise DatasetError(f"dataset contains personal data (row: {redact(text)[:80]!r}); redact it or "
                                   "choose pii_action=redact")
            text = redact(text)
        row = {"input": text}
        if label_col:
            row["label"] = str(r.get(label_col) or "").strip()
        clean_rows.append(row)
    with db.session() as s:
        logged = {_payload(p) for (p,) in s.execute(select(RequestLog.prompt).limit(20000))}
    duplicates = sum(1 for r in clean_rows if clean(r["input"]).lower() in logged)
    # rows already in the logs are flagged: build_dataset gives them the user's label (precedence) and keeps
    # evaluation rows out of training
    labels = Counter(r.get("label", "") for r in clean_rows if r.get("label"))
    ds_id = new_id("ds")
    body = "".join(json.dumps(r) + "\n" for r in clean_rows).encode()
    uri = blobstore.put(f"datasets/{ds_id}/data.jsonl", body)
    report = {"rows": len(clean_rows), "label_distribution": dict(labels.most_common()), "pii_rows": pii_rows,
              "pii_action": pii_action, "duplicates_of_logged_prompts": duplicates,
              "duplicates_within_file_removed": within_dupes, "columns": sorted(cols),
              "filename": filename}
    with db.session() as s:
        s.add(DatasetRow(id=ds_id, owner=owner, s3_uri=uri, columns_map=columns_map, use=use, rows=len(clean_rows),
                         pii_status="redacted" if pii_rows else "clean", report=report, created_at=time.time()))
    return {"id": ds_id, "use": use, "rows": len(clean_rows), "pii_status": "redacted" if pii_rows else "clean",
            "report": report}


def get(dataset_id: str) -> dict[str, Any]:
    with db.session() as s:
        d = s.get(DatasetRow, dataset_id)
        if d is None:
            raise DatasetError("dataset not found")
        return {"id": d.id, "owner": d.owner, "use": d.use, "rows": d.rows, "pii_status": d.pii_status,
                "report": d.report, "columns_map": d.columns_map}


def load(dataset_id: str) -> list[dict[str, str]]:
    body = blobstore.get(f"datasets/{dataset_id}/data.jsonl").decode()
    return [json.loads(x) for x in body.splitlines() if x.strip()]
