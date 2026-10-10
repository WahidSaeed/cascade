# Build progress

Claude Code keeps this file current. Read it first when resuming.

**Current step:** Phase 7 — Scale (in progress)
**Last `make verify`:** 2026-10-10 20:26 — PASS (Phases 5 + 6)

| Step | Status | Acceptance IDs | Evidence (last lines of `make verify`) | Commit |
|---|---|---|---|---|
| Step 0 — Foundation | ☑ done | — | lint pass · test-unit 29 passed · test-int 0 tests · contract pass · test-web 1 passed · test-e2e 0 tests · eval/perf/security pass · acceptance: missing 43 (no phase gated yet) · **Overall: PASS** 2026-10-09 19:46 | feat(step-0) |
| Phase 1 — Foundations | ☑ done | P1-01 … P1-10 (P1-10 aws-only) | lint pass · test-unit 121 passed · test-int 30 passed (Postgres) · contract pass · test-web 1 · test-e2e 0 · eval 4 · perf 1 (light p95 ≈ 12 ms) · security 4 · acceptance: pass 9, aws-only 1 · **Overall: PASS** 2026-10-10 13:47 | feat(phase-1) |
| Phase 2 — Next.js workspace | ☑ done | P2-01 … P2-04 | lint pass · test-unit 122 · test-int 30 · contract pass · test-web 10 · test-e2e 10 (S1–S3 + next dev) · eval 4 · perf 1 · security 4 · acceptance: pass 13, aws-only 1 · **Overall: PASS** 2026-10-10 14:27 | feat(phase-2) |
| Phase 3 — Knowledge base & RAG | ☑ done | P3-01 … P3-08 | lint pass · test-unit 146 · test-int 54 (pgvector) · contract pass · test-web 10 · test-e2e 14 (S1–S5 doc half) · eval 6 (recall@5 ≥ 0.9) · perf 1 · security 4 · acceptance: pass 22, aws-only 1 · **Overall: PASS** 2026-10-10 15:01 | feat(phase-3) |
| Phase 4 — Pattern intelligence | ☑ done | P4-01 … P4-03 | lint pass · test-unit 157 · test-int 63 · contract pass · test-web 10 · test-e2e 15 (incl. S8) · eval 6 · perf 1 · security 4 · acceptance: pass 25, aws-only 1 · **Overall: PASS** 2026-10-10 15:30 | feat(phase-4) |
| Phase 5 — Model-building agent | ☑ done | P5-01 … P5-07 | lint pass · test-unit 194 · test-int 71 · contract pass · test-web 10 · test-e2e 18 (S1–S9) · eval 6 · perf 1 · security 4 · acceptance: pass 38, aws-only 1 · **Overall: PASS** 2026-10-10 20:26 | feat(phase-5) |
| Phase 6 — Productionize | ☑ done | P6-01 … P6-06 | lint pass · test-unit 194 · test-int 71 · contract pass · test-web 10 · test-e2e 18 (S1–S9) · eval 6 · perf 1 · security 4 · acceptance: pass 38, aws-only 1 · **Overall: PASS** 2026-10-10 20:26 | feat(phase-6) |
| Phase 7 — Scale | ◐ in progress | P7-01 … P7-05 | | |
| Step D — Demo assembly | ☐ todo | S1 … S10 | | |

Status values: ☐ todo · ◐ in progress · ☑ done · ⚠ blocked

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
| S8 Drift protection | ☑ browser pass (inject/drift-check via API; /demo buttons in Step D) | web/e2e/demo/s8-*.spec.ts |
| S9 Integration (optional) | ☑ browser pass | web/e2e/demo/s9-*.spec.ts |
| S10 Real numbers on Bedrock (dry run only in the build) | ☑ dry run (backend/tests/unit/test_demo_measure.py) | backend/tests/…/test_demo_measure.py |

## Blocked
(none)

## Notes for the next session
- Phases 5 and 6 reviewed by the acceptance reviewer; all gaps fixed with regression tests
  (tests/unit/test_phase5_review.py, test_phase6_acceptance.py review tests).
- Phase 7 WIP (not committed): backend/app/light/pgindex.py (pgvector semantic cache + pattern kNN),
  backend/app/rollups.py (hourly ledger/overhead rollups), backend/tests/perf/locustfile.py. Still to do: migration
  0006 (HNSW on cache_entries, pattern_vectors, rollup tables), wire pgindex into light/cache.py + recognizer,
  rollups into engine/ledger writes + reads, read replica (Terraform + CASCADE_DATABASE_READ_URL), private-subnet
  no-NAT artifact Lambdas + S3 gateway endpoint, perf tests (1,000-prompt light p95, 60 s @ 50 rps, artifact p95),
  recall baseline file for P7-05.
