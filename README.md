# Cascade

Cascade sits between your users and an expensive LLM. Every prompt is monitored, logged and cached;
recurring patterns are compiled into deterministic artifacts (rules, classical ML, small deep-learning
models) that answer instead of the LLM — and every token spent, avoided and saved is accounted for,
net of the cost of building and checking the artifacts. Answer quality never drops below the LLM's.

BUILD.md is the specification; PROGRESS.md records where the build is; docs/decisions.md lists the
choices taken where the spec left room.

## Quick start (no Docker, no AWS)

```bash
make setup        # backend (uv) + web (npm) deps, Playwright browser, local models, demo data
make api          # API on :8080 against SQLite, Demo LLM, local auth
cd web && NEXT_PUBLIC_API_BASE=http://localhost:8080 npm run dev     # UI on :3000
```
`python -m app.seed --reset` (from `backend/`) loads the demo starting state: the fictional Northpeak
Outfitters week of traffic, four knowledge collections, and approved artifacts.

## Full local stack

```bash
make dev          # docker compose: postgres (pgvector), api, agent-worker, ingest-worker, web, crm-mock
make demo-up      # start, migrate and seed;  make demo-reset  returns to the starting state
```

## The gate

`make verify` runs every suite except real-AWS tests — lint/types, backend unit (SQLite) and integration
(Postgres + pgvector), API contract, web unit, Playwright E2E incl. the demo scenarios, evaluations,
performance and security — and writes `reports/summary.md`, `reports/coverage/` and
`reports/acceptance.md`. Real AWS is only touched by `make test-aws` and `make demo-measure`, run by hand
after deployment (DEPLOY.md). Every other target blanks AWS credentials.

## Layout

```
backend/app/       FastAPI app: light layer (app/light), router (engine.py), ledger, artifacts
                   (runtime.py, artifact_runner.py, deploy.py), knowledge base (app/rag), patterns, verifier
backend/alembic/   migrations (SQLite and PostgreSQL)
web/               Next.js (App Router) + Tailwind, themed like the Cascade deck
demo/              generator for the fictional demo world, documents, labelled evaluation sets
infra/terraform/   AWS: VPC, Aurora PostgreSQL Serverless v2 + pgvector, ECS Fargate, Lambda roles, SQS, S3
```
