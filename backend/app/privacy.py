"""PII screening (BUILD.md §7.5, rule 6). Regex locally; Amazon Comprehend in AWS (config switch).

`contains_pii` decides whether a cache entry may be global; `redact` is applied to prompts
before they are used for clustering, examples or training data.
"""
from __future__ import annotations

import re
from typing import Any

from .config import settings

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("ID", re.compile(r"\b[A-Z]{2,5}-\d{3,}\b")),
    ("NAME", re.compile(r"\b(?:my name is|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")),
]


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def find_pii(text: str) -> list[tuple[str, str]]:
    if settings.pii_backend == "comprehend":
        return _comprehend(text)
    found: list[tuple[str, str]] = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            val = m.group(1) if kind == "NAME" else m.group(0)
            if kind == "CARD" and not _luhn_ok(val):
                continue
            found.append((kind, val))
    return found


def contains_pii(text: str) -> bool:
    return bool(find_pii(text))


def redact(text: str) -> str:
    out = text
    for kind, val in sorted(find_pii(text), key=lambda kv: -len(kv[1])):
        out = out.replace(val, f"<{kind}>")
    return out


_client: Any = None


def _comprehend(text: str) -> list[tuple[str, str]]:  # exercised with botocore Stubber in tests
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("comprehend", region_name=settings.bedrock_region)
    resp = _client.detect_pii_entities(Text=text[:5000], LanguageCode="en")
    return [(e["Type"], text[e["BeginOffset"]:e["EndOffset"]]) for e in resp.get("Entities", [])
            if e.get("Score", 0) >= 0.5]
