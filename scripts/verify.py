"""`make verify`: the single gate (BUILD.md §14).

Runs every suite except the opt-in AWS tests, then writes:
  reports/summary.md     one line per suite: pass/fail, counts, duration
  reports/coverage/      (written by test-unit)
  reports/acceptance.md  (scripts/acceptance_matrix.py)
Exits non-zero if any suite fails or any acceptance ID is `fail` or `missing`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

# (make target, junit file or None)
SUITES: list[tuple[str, str | None]] = [
    ("lint", None),
    ("test-unit", "unit.xml"),
    ("test-int", "int.xml"),
    ("contract", None),
    ("test-web", "web.xml"),
    ("test-e2e", "e2e.xml"),
    ("eval", "eval.xml"),
    ("perf", "perf.xml"),
    ("security", "security.xml"),
]


def junit_counts(path: Path) -> str:
    if not path.exists():
        return "0 tests"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return "unreadable report"
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = fail = err = skip = 0
    for s in suites:
        tests += int(s.get("tests", 0))
        fail += int(s.get("failures", 0))
        err += int(s.get("errors", 0))
        skip += int(s.get("skipped", 0))
    if root.tag == "testsuites" and not suites:
        tests = int(root.get("tests", 0))
        fail = int(root.get("failures", 0))
    passed = tests - fail - err - skip
    return f"{tests} tests: {passed} passed, {fail + err} failed, {skip} skipped/xfail"


def main() -> int:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "junit").mkdir(exist_ok=True)
    only = os.environ.get("VERIFY_ONLY", "").split(",") if os.environ.get("VERIFY_ONLY") else None
    for stale in (REPORTS / "junit").glob("*.xml"):
        stale.unlink()
    for stale in (REPORTS / "acceptance").glob("*.json") if (REPORTS / "acceptance").exists() else []:
        stale.unlink()

    lines = ["# make verify — summary", "", "| Suite | Result | Counts | Duration |", "|---|---|---|---|"]
    ok_all = True
    env = dict(os.environ, CONTRACT_CHECK="1")
    for target, junit in SUITES:
        if only and target not in only:
            continue
        print(f"\n=== make {target} ===", flush=True)
        t0 = time.time()
        log = REPORTS / f"{target}.log"
        with log.open("w") as fh:
            proc = subprocess.Popen(["make", "-s", "-C", str(ROOT), target], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=env)
            assert proc.stdout is not None
            for line in proc.stdout:
                fh.write(line)
                sys.stdout.write(line)
            rc = proc.wait()
        dur = time.time() - t0
        counts = junit_counts(REPORTS / "junit" / junit) if junit else "—"
        ok = rc == 0
        ok_all &= ok
        lines.append(f"| {target} | {'pass' if ok else 'FAIL'} | {counts} | {dur:.1f}s |")
        print(f"=== {target}: {'pass' if ok else 'FAIL'} ({counts}, {dur:.1f}s) ===", flush=True)

    print("\n=== acceptance matrix ===", flush=True)
    acc = subprocess.run([sys.executable, str(ROOT / "scripts" / "acceptance_matrix.py")], text=True,
                         capture_output=True)
    print(acc.stdout + acc.stderr)
    acc_ok = acc.returncode == 0
    lines.append(f"| acceptance matrix | {'pass' if acc_ok else 'FAIL'} | {acc.stdout.strip().splitlines()[-1] if acc.stdout.strip() else ''} | — |")
    ok_all &= acc_ok

    lines += ["", f"**Overall: {'PASS' if ok_all else 'FAIL'}** — {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
