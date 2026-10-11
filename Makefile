# Cascade — the only entry point (BUILD.md §15). `make verify` is the single gate (§14).
SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BACKEND := $(ROOT)/backend
WEB := $(ROOT)/web
REPORTS := $(ROOT)/reports
export PATH := $(HOME)/.local/bin:$(PATH)

# §14.2: a stray boto3 call can never reach a real account. The blanked credentials are
# exported to EVERY recipe; only test-aws and demo-measure restore the caller's environment.
_AWS_VARS := AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_DEFAULT_REGION AWS_REGION \
	AWS_CONFIG_FILE AWS_SHARED_CREDENTIALS_FILE AWS_PROFILE
REAL_AWS_ENV := $(foreach v,$(_AWS_VARS),$(if $(value $(v)),$(v)='$(value $(v))'))
REAL_AWS := env $(foreach v,$(_AWS_VARS),-u $(v)) $(REAL_AWS_ENV)
export AWS_ACCESS_KEY_ID := testing
export AWS_SECRET_ACCESS_KEY := testing
export AWS_SESSION_TOKEN := testing
export AWS_DEFAULT_REGION := us-east-1
export AWS_REGION := us-east-1
export AWS_CONFIG_FILE := /dev/null
export AWS_SHARED_CREDENTIALS_FILE := /dev/null
unexport AWS_PROFILE
SAFE_AWS :=
TEST_ENV := $(SAFE_AWS) CASCADE_LLM=demo CASCADE_DEMO_LATENCY_MS=0 CASCADE_DRIFT_SAMPLE_RATE=0 CASCADE_MODELS_DIR=$(ROOT)/.models \
	HF_HUB_OFFLINE=$${HF_HUB_OFFLINE:-1} TOKENIZERS_PARALLELISM=false
UV := cd $(BACKEND) && $(TEST_ENV) uv run
# pytest exit code 5 = "no tests collected": a suite with no tests yet reports 0 tests, not a failure.
NOTESTS_OK := ; rc=$$?; if [ $$rc -eq 5 ]; then echo "0 tests"; exit 0; fi; exit $$rc
PG_URL := postgresql+psycopg://cascade:cascade@localhost:5433/cascade
COMPOSE := docker compose -f $(ROOT)/docker-compose.yml

.PHONY: help setup models dev lint test-unit test-int test-web test-e2e contract eval perf security \
	verify test-aws pg-up pg-down demo-up demo-reset demo-e2e demo-e2e-stack demo-record demo-measure demo-data \
	web-build api seed clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n",$$1,$$2}'

setup: ## install backend (uv) + web (npm) deps, Playwright browser, local models, demo data
	cd $(BACKEND) && uv sync --python 3.12
	cd $(WEB) && npm install --no-audit --no-fund
	cd $(WEB) && npx playwright install chromium
	$(MAKE) models demo-data

models: ## download the light-layer embedder, reranker and tiny DL base model into .models/
	$(UV) env HF_HUB_OFFLINE=0 python -m app.models_download

demo-data: ## generate the fictional Northpeak demo world, labelled sets and documents
	$(UV) python $(ROOT)/demo/generate.py

dev: ## local stack via docker compose (Demo LLM, local auth)
	$(COMPOSE) up --build

api: ## run the API on :8080 against SQLite (no Docker quick start)
	$(UV) env CASCADE_DEMO=true uvicorn app.main:app --port 8080 --reload

pg-up: ## start the docker-compose Postgres (pgvector) and wait for it
	$(COMPOSE) up -d postgres
	@for i in $$(seq 1 60); do $(COMPOSE) exec -T postgres pg_isready -U cascade >/dev/null 2>&1 && exit 0; sleep 1; done; echo "postgres not ready"; exit 1

pg-down:
	$(COMPOSE) stop postgres

lint: ## ruff, mypy (strict modules), eslint, tsc, terraform fmt/validate, actionlint
	$(UV) ruff check app tests ../demo ../scripts
	$(UV) mypy app
	cd $(WEB) && npx eslint . --max-warnings=0 && npx tsc --noEmit
	cd $(ROOT)/infra/terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate
	@if command -v actionlint >/dev/null; then actionlint; else echo "actionlint not installed: skipped"; fi

test-unit: ## backend unit tests (no network, no Docker) with coverage
	@mkdir -p $(REPORTS)/junit $(REPORTS)/acceptance
	$(UV) pytest tests/unit -m "not aws and not integration" --disable-socket --allow-unix-socket \
		--junitxml=$(REPORTS)/junit/unit.xml --cov=app --cov-report=html:$(REPORTS)/coverage \
		--cov-report=json:$(REPORTS)/coverage/coverage.json --cov-report=term:skip-covered
	$(UV) python $(ROOT)/scripts/coverage_gate.py $(REPORTS)/coverage/coverage.json

test-int: pg-up ## Postgres + pgvector integration tests
	@mkdir -p $(REPORTS)/junit
	$(UV) env CASCADE_TEST_DATABASE_URL=$(PG_URL) pytest tests/integration -m "not aws" \
		--junitxml=$(REPORTS)/junit/int.xml $(NOTESTS_OK)

contract: ## OpenAPI -> web/lib/api-types.ts; fails if regenerating changes the file
	$(UV) python -m app.export_openapi $(REPORTS)/openapi.json
	cd $(WEB) && npx openapi-typescript $(REPORTS)/openapi.json -o lib/api-types.ts.new >/dev/null
	@if [ -f $(WEB)/lib/api-types.ts ] && cmp -s $(WEB)/lib/api-types.ts $(WEB)/lib/api-types.ts.new; then \
		rm $(WEB)/lib/api-types.ts.new; echo "contract: api-types.ts up to date"; \
	else mv $(WEB)/lib/api-types.ts.new $(WEB)/lib/api-types.ts; \
		if [ -n "$$CONTRACT_CHECK" ]; then echo "contract: api-types.ts was stale (regenerated)"; exit 1; fi; \
		echo "contract: regenerated api-types.ts"; fi

test-web: ## Vitest + React Testing Library
	@mkdir -p $(REPORTS)/junit
	cd $(WEB) && npx vitest run --passWithNoTests --reporter=default --reporter=junit --outputFile=$(REPORTS)/junit/web.xml

web-build: ## next build (static export) into web/out
	cd $(WEB) && npx next build

test-e2e: ## Playwright against the full local stack (feature tests + demo scenarios)
	@mkdir -p $(REPORTS)/junit
	cd $(WEB) && $(TEST_ENV) PW_JUNIT=$(REPORTS)/junit/e2e.xml npx playwright test --pass-with-no-tests

eval: ## quality evaluations with thresholds (§14.4)
	@mkdir -p $(REPORTS)/junit
	$(UV) pytest tests/eval --junitxml=$(REPORTS)/junit/eval.xml $(NOTESTS_OK)

perf: pg-up ## light-layer latency, artifact route, 60 s load test at 50 rps on Postgres (§13 targets)
	@mkdir -p $(REPORTS)/junit
	$(UV) env CASCADE_TEST_DATABASE_URL=$(PG_URL) pytest tests/perf --junitxml=$(REPORTS)/junit/perf.xml -s $(NOTESTS_OK)

security: ## pip-audit, bandit, npm audit, gitleaks (if installed), isolation tests
	@mkdir -p $(REPORTS)/junit
	$(UV) bandit -q -r app -lll
	$(UV) pip-audit --progress-spinner off --skip-editable $${PIP_AUDIT_EXTRA:-} || (echo "pip-audit: see output above"; exit 1)
	cd $(WEB) && npm audit --omit=dev --audit-level=high
	@if command -v gitleaks >/dev/null; then gitleaks detect --no-banner -s $(ROOT); else echo "gitleaks not installed: skipped"; fi
	$(UV) pytest tests/security --disable-socket --allow-unix-socket --junitxml=$(REPORTS)/junit/security.xml $(NOTESTS_OK)

verify: ## THE gate: every suite except AWS; writes reports/summary.md, coverage, acceptance.md
	@cd $(BACKEND) && $(TEST_ENV) uv run python $(ROOT)/scripts/verify.py

test-aws: ## real AWS sandbox tests (manual; never during the build)
	cd $(BACKEND) && $(REAL_AWS) CASCADE_LLM=bedrock uv run pytest tests -m aws

DEMO_API := http://localhost:8080

demo-up: ## start the stack, migrate and seed
	$(COMPOSE) up -d --build
	@echo "waiting for the API (it migrates the database on start)…"
	@for i in $$(seq 1 120); do curl -fsS $(DEMO_API)/api/health >/dev/null 2>&1 && break; sleep 2; done; \
		curl -fsS $(DEMO_API)/api/health >/dev/null || { echo "API did not come up"; exit 1; }
	# the seed trains the urgency dl artifact once: run it where torch is installed (the agent-worker image), in a
	# one-off container while the worker itself is stopped (Docker Desktop's VM has little memory to spare)
	$(COMPOSE) stop agent-worker
	$(COMPOSE) run --rm --no-deps agent-worker python -m app.seed --reset
	$(COMPOSE) start agent-worker
	# the running API restores the snapshot itself, so its artifact workers and caches start clean
	curl -fsS -X POST $(DEMO_API)/api/demo/reset && echo

demo-reset: ## return the demo to its starting state (< 30 s)
	@if curl -fsS $(DEMO_API)/api/health >/dev/null 2>&1; then \
		curl -fsS -X POST $(DEMO_API)/api/demo/reset && echo; \
	else $(UV) python -m app.seed --reset; fi

demo-e2e: ## every demo scenario, headless
	@mkdir -p $(REPORTS)/junit
	cd $(WEB) && $(TEST_ENV) PW_JUNIT=$(REPORTS)/junit/demo.xml npx playwright test --grep @demo

demo-e2e-stack: ## every demo scenario against the running compose stack (make demo-up first) — the stage check
	cd $(WEB) && PW_STACK=compose npx playwright test --grep @demo

demo-record: ## record a video of each scenario into demo/backup/
	cd $(WEB) && $(TEST_ENV) PW_RECORD=1 npx playwright test --grep @demo
	$(UV) python $(ROOT)/scripts/collect_videos.py $(WEB)/test-results $(ROOT)/demo/backup

demo-measure: ## S10: replay a 200-prompt sample against real Bedrock (manual; needs DEPLOY.md setup)
	cd $(BACKEND) && $(REAL_AWS) CASCADE_LLM=bedrock uv run python -m app.demo_measure --max-usd $${MAX_USD:-5}

clean:
	rm -rf $(REPORTS) $(WEB)/out $(WEB)/.next $(BACKEND)/.pytest_cache
