"""Write the API's OpenAPI schema to a file (make contract)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .main import app


def main() -> None:
    Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
