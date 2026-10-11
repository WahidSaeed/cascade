"""Write the API's OpenAPI schema to a file (make contract)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import settings

settings.demo = True          # the contract includes the demo endpoints the /demo page calls (mounted at import)
from .main import app  # noqa: E402


def main() -> None:
    Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
