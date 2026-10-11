"""Copy Playwright scenario videos into demo/backup/ (make demo-record), one per scenario, named after its spec:
web/e2e/demo/s1-live-routing.spec.ts -> demo/backup/S1-live-routing.webm (the names demo/SCRIPT.md links).

Playwright names each result folder "<spec path slug>-<test title slug>" and shortens long ones with a hash, so
the spec file name — always intact at the start — is what identifies the scenario."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path


def target_name(folder: str, specs: list[str]) -> str:
    for spec in sorted(specs, key=len, reverse=True):              # longest match first
        if folder.startswith(f"demo-{spec}-") or folder == f"demo-{spec}":
            return re.sub(r"^s(\d+)-", r"S\1-", spec) + ".webm"
    return folder + ".webm"


def main(src: str, dst: str) -> None:
    results = Path(src)
    specs = [p.name.removesuffix(".spec.ts") for p in (results.parent / "e2e" / "demo").glob("*.spec.ts")]
    out = Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.webm"):
        old.unlink()
    n = 0
    for video in sorted(results.rglob("*.webm")):
        shutil.copy(video, out / target_name(video.parent.name, specs))
        n += 1
    print(f"collected {n} videos into {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
