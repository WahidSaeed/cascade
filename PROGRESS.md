# Build progress

Claude Code keeps this file current. Read it first when resuming.

**Current step:** Build complete — all steps done (Step 0, Phases 1–7, Step D)
**Last `make verify`:** 2026-10-11 04:23 — PASS (Step D, whole build)

| Step | Status | Acceptance IDs | Evidence (last lines of `make verify`) | Commit |
|---|---|---|---|---|
| Step 0 — Foundation | ☑ done | — | lint pass · test-unit 29 passed · test-int 0 tests · contract pass · test-web 1 passed · test-e2e 0 tests · eval/perf/security pass · acceptance: missing 43 (no phase gated yet) · **Overall: PASS** 2026-10-09 19:46 | feat(step-0) |
| Phase 1 — Foundations | ☑ done | P1-01 … P1-10 (P1-10 aws-only) | lint pass · test-unit 121 passed · test-int 30 passed (Postgres) · contract pass · test-web 1 · test-e2e 0 · eval 4 · perf 1 (light p95 ≈ 12 ms) · security 4 · acceptance: pass 9, aws-only 1 · **Overall: PASS** 2026-10-10 13:47 | feat(phase-1) |
| Phase 2 — Next.js workspace | ☑ done | P2-01 … P2-04 | lint pass · test-unit 122 · test-int 30 · contract pass · test-web 10 · test-e2e 10 (S1–S3 + next dev) · eval 4 · perf 1 · security 4 · acceptance: pass 13, aws-only 1 · **Overall: PASS** 2026-10-10 14:27 | feat(phase-2) |
| Phase 3 — Knowledge base & RAG | ☑ done | P3-01 … P3-08 | lint pass · test-unit 146 · test-int 54 (pgvector) · contract pass · test-web 10 · test-e2e 14 (S1–S5 doc half) · eval 6 (recall@5 ≥ 0.9) · perf 1 · security 4 · acceptance: pass 22, aws-only 1 · **Overall: PASS** 2026-10-10 15:01 | feat(phase-3) |
| Phase 4 — Pattern intelligence | ☑ done | P4-01 … P4-03 | lint pass · test-unit 157 · test-int 63 · contract pass · test-web 10 · test-e2e 15 (incl. S8) · eval 6 · perf 1 · security 4 · acceptance: pass 25, aws-only 1 · **Overall: PASS** 2026-10-10 15:30 | feat(phase-4) |
| Phase 5 — Model-building agent | ☑ done | P5-01 … P5-07 | lint pass · test-unit 194 · test-int 71 · contract pass · test-web 10 · test-e2e 18 (S1–S9) · eval 6 · perf 1 · security 4 · acceptance: pass 38, aws-only 1 · **Overall: PASS** 2026-10-10 20:26 | feat(phase-5) |
| Phase 6 — Productionize | ☑ done | P6-01 … P6-06 | lint pass · test-unit 194 · test-int 71 · contract pass · test-web 10 · test-e2e 18 (S1–S9) · eval 6 · perf 1 · security 4 · acceptance: pass 38, aws-only 1 · **Overall: PASS** 2026-10-10 20:26 | feat(phase-6) |
| Phase 7 — Scale | ☑ done | P7-01 … P7-05 | lint pass · test-unit 218 · test-int 81 (pgvector) · contract pass · test-web 10 · test-e2e 18 (S1–S9) · eval 6 (recall@5 1.000 = Phase 3 baseline) · perf 8 (light p95 11.3 ms SQLite / 13.5 ms Postgres over 1,000 prompts; knowledge-scoped 10.9 / 19.9 ms; artifact route 18.3 / 29.2 ms; 60 s load 49.9 rps, 0 failures, p95 750 ms) · security 4 · acceptance: pass 42, aws-only 1 · **Overall: PASS** 2026-10-11 00:53 | feat(phase-7) |
| Step D — Demo assembly | ☑ done | S1 … S10 | lint pass · test-unit 226 · test-int 82 (pgvector) · contract pass · test-web 10 · test-e2e 19 (S1–S9 + /demo page) · eval 6 · perf 8 (light p95 11.1 / 13.1 ms, knowledge-scoped 15.7 ms, artifact route 20.8 ms, 60 s load 49.9 rps, 0 failures, p95 560 ms) · security 4 · acceptance: pass 42, aws-only 1 · **Overall: PASS** 2026-10-11 04:23 · also: `make demo-e2e` 10/10, `make demo-up` + `make demo-e2e-stack` 10/10 on the compose stack, `make demo-record` 10 videos | feat(step-d) |

Status values: ☐ todo · ◐ in progress · ☑ done · ⚠ blocked

Evidence cells are the `make verify` output as printed at the time. The acceptance totals in the Phase 3–6 rows are
one higher than the number of IDs gated then (noted by the whole-build review; the cause was not re-investigated —
the matrix counts every acceptance row it finds a passing test for). The final matrix is exact: 42 pass + 1 aws-only.

## Demo scenarios (BUILD.md §17.2)
| Scenario | Status | Test |
|---|---|---|
| S1 Live routing | ☑ browser pass | web/e2e/demo/s1-*.spec.ts |
| S2 Safety rails | ☑ browser pass (incl. low-confidence fallback on the urgency dl artifact) | web/e2e/demo/s2-*.spec.ts |
| S3 Savings dashboard | ☑ browser pass | web/e2e/demo/s3-*.spec.ts |
| S4 Knowledge answers, shared safely | ☑ browser pass | web/e2e/demo/s4-*.spec.ts |
| S5 Documents change, answers stay correct | ☑ browser pass (incl. rebuild of the lookup from v2) | web/e2e/demo/s5-*.spec.ts |
| S6 Pattern → agent → trained model | ☑ browser pass | web/e2e/demo/s6-*.spec.ts |
| S7 Lookup from a document | ☑ browser pass | web/e2e/demo/s7-*.spec.ts |
| S8 Drift protection | ☑ browser pass (via the /demo buttons) | web/e2e/demo/s8-*.spec.ts |
| S9 Integration (optional) | ☑ browser pass | web/e2e/demo/s9-*.spec.ts |
| S10 Real numbers on Bedrock (dry run only in the build) | ☑ dry run (backend/tests/unit/test_demo_measure.py) | backend/tests/…/test_demo_measure.py |

## Blocked
(none)

## Notes for the next session
- The build is complete. Before the event: `make demo-up`, then `make demo-e2e-stack` once (every scenario against
  the compose stack used on stage); `make demo-reset` between rehearsals; `demo/SCRIPT.md` is the run-of-show and
  `demo/backup/` holds the fallback videos.
- Whole-build review (Step D) gaps all fixed with tests: compose image build, migration lock, seed latency/memory,
  sync-run lease, resilient workers/heartbeat/config sync, locale-free ML artifacts, knowledge answers excluded
  from distillation labels, CRM URL, stale persona data, replay/reset races, demo off by default, /demo buttons
  covered by browser tests, theme test over every static view.
- Post-build: the agent-worker image now uses the CPU-only torch wheel on Linux, 6.52 GB -> 1.81 GB
  (`make demo-up` exit 0 with the dl artifact trained in the image; `make demo-e2e-stack` 10 passed).
  Full `make verify` after the change: **Overall: PASS** — 2026-10-11 13:47:02 (unit 226, int 82, web 10,
  e2e 19, eval 6, perf 8, security 4; acceptance pass 42, aws-only 1).
- Note: `make perf` is sensitive to other load on the build machine (thresholds unchanged).

## Post-build: workspace UI and editable ml / dl code (2026-10-11, user request)
- Readable Python for every ml / dl artifact (`Pattern.source_code`: exact `train()` recipe + serving `handle()`), proven equivalent to the served model.
- Edited ml / dl code trains as a sandboxed job (local: separate process, clean env, nobody uid in containers, limits, kill on timeout; AWS: network-isolated SageMaker job via infra/runtime-images/training.Dockerfile), is scored inside the job, goes through the holdout gate and admin approval, then serves as written.
- The web app is a workspace: activity bar, Patterns explorer, Monaco artifact editor (tabs, ⌘S, Test / Output / Problems, inspector), status bar.
- Evidence: `make verify` **Overall: PASS** — 2026-10-11 16:35:57 (unit 239, int 82, web 10, e2e 22, eval 6, perf 8 — load 50.0 rps, 0 failures, p95 690 ms — security 4; acceptance pass 42, aws-only 1).

## Post-build: admin control centre (2026-10-13, user request)
- Per artifact: version history (what produced each version, status, verification) with diffs and one-click rollback,
  usage across users (served by the artifact vs still on the LLM, tokens and cost avoided, per user), and retrain with
  more data / an approved integration (ml / dl) or rebuild with the agent (rules).
- Evidence: `make verify` 2026-10-13 06:33:32 on an overloaded machine (load average up to 38): lint, unit 246,
  int 82, contract, web 10, **e2e 25/25** (incl. control-centre history/diff/rollback, usage, retrain and the editor
  specs), eval 6, security 4 pass; **perf 7/8** — sqlite light-layer p95 29.0 ms vs the 25 ms target (Postgres 19.4 ms;
  load test 48.4 rps, 0 failures). A perf-only re-run was uniformly ~2x the 2026-10-11 baseline on paths this work does
  not touch, i.e. machine contention; re-run `make perf` on a quiet machine to confirm.
- Local Postgres healthcheck relaxed (pg_isready -t 20, interval 10s, timeout 30s): under load Docker killed
  pg_isready mid-handshake and the starting backend died of SIGPIPE, which restarted every Postgres connection.

## Post-build: real-Bedrock checks P1-10 and S10 (2026-10-19, user request)
- Run by hand with the user's credentials in eu-central-1 (`eu.` Claude Sonnet 4.5 heavy / Haiku 4.5 light, prices for
  the `eu.` ids passed via `CASCADE_MODEL_PRICES`). **P1-10** `make test-aws`: 1 passed. **S10** `make demo-measure`
  (cap $5): measured, 200 prompts replayed, routes artifact 59 / cache 27 / llm 114, token reduction 28.3 %, actual
  spend $0.13, net saved $-0.015 (one-off build overhead exceeds the savings on a 200-prompt sample) — `reports/measured.md`.
- The first real-LLM run of compile/verify exposed bugs the Demo LLM hid, all fixed with tests
  (`tests/unit/test_real_llm_compile.py`): redacted examples now keep identifier shapes (`<ID:AA-99999>`) and the
  compile prompt explains placeholders; one output format per task type is sent to both the compile and the reference
  (verify / drift) calls; compile gets its own token limit (`compile_max_tokens`); unclosed code fences; JSON replies
  read through fences and surrounding text (`parse_json_reply`: verifier, lookup table, discovery); transform answers
  compare numbers by value; the scripted agent no longer sends its turn to a real LLM; cachePoint only for models
  that accept it; per-category overhead rounded like the other ledger totals.
- Evidence: `make lint` pass; `make test-unit` 254 passed; `make test-int` 82 passed (2026-10-19 18:10);
  `make test-e2e` 25 passed. `make perf` 8 passed (2026-10-19 18:40; light p95 14.0 ms SQLite / 17.8 ms Postgres,
  artifact route 13.8 / 25.9 ms, load 48.3 rps, 0 failures, p95 800 ms).

## Post-build: AWS deployment for public testing (2026-10-19, user request)
- Pushed to https://github.com/WahidSaeed/cascade (public). Deployed to eu-central-1 (no-domain mode):
  https://d1xzxi7wla3zp5.cloudfront.net, Cognito admin created. `model_prices` Terraform variable carries the `eu.` prices.
- Seeded with the one-off `cascade-seed` task (DEPLOY.md §6a): 2,400 requests replayed on real Bedrock, 4 artifacts
  installed, 2 patterns recognized. Afterwards: extraction and conversion approved and serving; the urgency dl
  artifact was demoted by the hourly drift job (78 % agreement with real Sonnet); the vacation lookup stayed a draft
  (verification 0.6 against real Sonnet, lookups need 1.0) — not yet investigated.
- Real-AWS bugs found and fixed with tests: drafts could never verify with the lambda runtime (now a temporary
  `-verify` Lambda); Lambda could not pull the ml / dl runtime images (repository policy + ECR read); the hot cache
  used multi-key commands that ElastiCache Serverless (cluster mode) rejects (now single-key pipelines).
- Account limit: SageMaker training quota is 0 (ml.m5.large); the seed trains the dl artifact in-task.
- Evidence: `make lint` pass; `make test-unit` 255 passed (one load-sensitive flake in test_source_training passed
  3/3 on re-run); `make test-int` 82 passed.
