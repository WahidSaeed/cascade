"""Copy Playwright scenario videos into demo/backup/ (make demo-record)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main(src: str, dst: str) -> None:
    out = Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for video in Path(src).rglob("*.webm"):
        name = video.parent.name.split("-")[0:4]
        shutil.copy(video, out / ("-".join(name) + ".webm"))
        n += 1
    print(f"collected {n} videos into {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
