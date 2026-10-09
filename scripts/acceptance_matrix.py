"""Acceptance matrix (BUILD.md §14.3).

Reads every acceptance ID (P1-01 …) and its description from BUILD.md, and the test results:
  - pytest: reports/acceptance/*.json, written by backend/tests/conftest.py
  - Playwright: reports/junit/e2e.xml and demo.xml, where a test title carries `@P2-01`
Writes reports/acceptance.md with one row per ID: description, covering tests, status
(pass | fail | missing | aws-only). Exits 1 if any ID is fail or missing.

`aws-only`: every non-AWS test for the ID passed, and at least one test marked `aws` covers
the behaviour that needs the real service (BUILD.md §14.3).
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
ID_RE = re.compile(r"\*\*(P\d-\d\d)\*\*\s*(.+)")


def read_ids() -> dict[str, str]:
    ids: dict[str, str] = {}
    lines = (ROOT / "BUILD.md").read_text().splitlines()
    for i, line in enumerate(lines):
        m = ID_RE.search(line)
        if not m:
            continue
        desc = m.group(2).strip()
        j = i + 1
        while j < len(lines) and lines[j].startswith("      ") and not ID_RE.search(lines[j]):
            desc += " " + lines[j].strip()
            j += 1
        ids[m.group(1)] = desc
    return ids


def main() -> int:
    ids = read_ids()
    # id -> list of (test name, outcome)  outcome in passed|failed|skipped|xfailed|aws
    results: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for f in sorted((REPORTS / "acceptance").glob("*.json")) if (REPORTS / "acceptance").exists() else []:
        for row in json.loads(f.read_text()):
            for aid in row["ids"]:
                results[aid].append((row["nodeid"], row["outcome"]))

    for name in ("e2e.xml", "demo.xml"):
        p = REPORTS / "junit" / name
        if not p.exists():
            continue
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        for tc in root.iter("testcase"):
            title = tc.get("name", "")
            tags = re.findall(r"@(P\d-\d\d)", title)
            if not tags:
                continue
            if tc.find("failure") is not None or tc.find("error") is not None:
                outcome = "failed"
            elif tc.find("skipped") is not None:
                outcome = "skipped"
            else:
                outcome = "passed"
            for aid in tags:
                results[aid].append((f"playwright: {title}", outcome))

    rows = ["# Acceptance matrix", "", "| ID | Description | Tests | Status |", "|---|---|---|---|"]
    counts: dict[str, int] = defaultdict(int)
    statuses: dict[str, str] = {}
    for aid, desc in ids.items():
        tests = results.get(aid, [])
        ran = [o for _, o in tests if o != "aws"]
        has_aws = any(o == "aws" for _, o in tests)
        if any(o in ("failed", "xfailed") for o in ran):
            status = "fail"
        elif not ran or all(o == "skipped" for o in ran):
            status = "missing"
        elif has_aws or any(o == "xfailed-aws" for o in ran):
            status = "aws-only"
        else:
            status = "pass"
        counts[status] += 1
        statuses[aid] = status
        names = "<br>".join(sorted({f"`{n}` ({o})" for n, o in tests})) or "—"
        short = (desc[:160] + "…") if len(desc) > 160 else desc
        rows.append(f"| {aid} | {short.replace('|', '/')} | {names} | **{status}** |")

    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    rows += ["", f"Totals — {summary}"]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "acceptance.md").write_text("\n".join(rows) + "\n")
    # The gate covers the phases built so far (scripts/acceptance_gate.txt, e.g. "P1 P2");
    # IDs of later phases are reported but cannot fail the gate before their phase starts.
    gate_file = ROOT / "scripts" / "acceptance_gate.txt"
    gated = sys.argv[1:] or (gate_file.read_text().split() if gate_file.exists() else [])
    bad = [a for a, st in statuses.items() if a.split("-")[0] in gated and st in ("fail", "missing")]
    if bad:
        print("failing or missing:", " ".join(bad))
    print(f"acceptance: {summary}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
