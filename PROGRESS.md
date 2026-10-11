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
- Optional, not blocking: the agent-worker image is 6.5 GB (torch for the one-off local dl training);
  `make perf` is sensitive to other load on the build machine (thresholds unchanged).
