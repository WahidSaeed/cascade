"""Coverage gate (BUILD.md §14.2): >= 85 % on the core modules, >= 75 % overall."""
from __future__ import annotations

import json
import sys
from pathlib import Path

CORE = ("app/light/", "app/rag/", "app/ledger.py", "app/engine.py", "app/verifier.py")
CORE_MIN, OVERALL_MIN = 85.0, 75.0


def main(path: str) -> int:
    data = json.loads(Path(path).read_text())
    files = data["files"]
    overall = data["totals"]["percent_covered"]
    cov = miss = 0
    for name, f in files.items():
        norm = name.replace("\\", "/")
        if any(norm.endswith(c) or c in norm for c in CORE):
            cov += f["summary"]["covered_lines"]
            miss += f["summary"]["missing_lines"]
    core = 100.0 * cov / (cov + miss) if (cov + miss) else 100.0
    print(f"coverage: core {core:.1f}% (min {CORE_MIN}), overall {overall:.1f}% (min {OVERALL_MIN})")
    if core < CORE_MIN or overall < OVERALL_MIN:
        print("coverage gate FAILED")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
