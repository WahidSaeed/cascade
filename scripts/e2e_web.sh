#!/usr/bin/env bash
# Static export of web/ pointed at the E2E API, served like production (S3 + CloudFront stand-in).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/web"
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8181 npx next build >/dev/null
exec npx serve -s out -l 3181 --no-clipboard
