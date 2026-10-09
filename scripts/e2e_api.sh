#!/usr/bin/env bash
# API for Playwright: Demo LLM, local auth, a fresh database in the demo-reset state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
export CASCADE_LLM=demo CASCADE_DEMO_LATENCY_MS=${CASCADE_DEMO_LATENCY_MS:-0} CASCADE_AUTH=local CASCADE_DEMO=true
export CASCADE_DATA_DIR="$ROOT/reports/e2e-data" CASCADE_MODELS_DIR="$ROOT/.models" HF_HUB_OFFLINE=1
export CASCADE_DATABASE_URL="${E2E_DATABASE_URL:-sqlite:///$ROOT/reports/e2e-data/cascade.db}"
export CASCADE_CRM_URL="http://127.0.0.1:8191"
export AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing AWS_DEFAULT_REGION=us-east-1
export AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null
mkdir -p "$CASCADE_DATA_DIR"
uv run python -m app.seed --reset
uv run uvicorn app.crm_mock:app --host 127.0.0.1 --port 8191 --log-level warning &
CRM=$!
trap 'kill $CRM 2>/dev/null || true' EXIT
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8181 --log-level warning
