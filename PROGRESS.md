# Build progress

Claude Code keeps this file current. Read it first when resuming.

**Current step:** Phase 5 — Model-building agent (in progress)
**Last `make verify`:** 2026-10-10 15:30 — PASS (Phase 4)

| Step | Status | Acceptance IDs | Evidence (last lines of `make verify`) | Commit |
|---|---|---|---|---|
| Step 0 — Foundation | ☑ done | — | lint pass · test-unit 29 passed · test-int 0 tests · contract pass · test-web 1 passed · test-e2e 0 tests · eval/perf/security pass · acceptance: missing 43 (no phase gated yet) · **Overall: PASS** 2026-10-09 19:46 | feat(step-0) |
| Phase 1 — Foundations | ☑ done | P1-01 … P1-10 (P1-10 aws-only) | lint pass · test-unit 121 passed · test-int 30 passed (Postgres) · contract pass · test-web 1 · test-e2e 0 · eval 4 · perf 1 (light p95 ≈ 12 ms) · security 4 · acceptance: pass 9, aws-only 1 · **Overall: PASS** 2026-10-10 13:47 | feat(phase-1) |
| Phase 2 — Next.js workspace | ☑ done | P2-01 … P2-04 | lint pass · test-unit 122 · test-int 30 · contract pass · test-web 10 · test-e2e 10 (S1–S3 + next dev) · eval 4 · perf 1 · security 4 · acceptance: pass 13, aws-only 1 · **Overall: PASS** 2026-10-10 14:27 | feat(phase-2) |
| Phase 3 — Knowledge base & RAG | ☑ done | P3-01 … P3-08 | lint pass · test-unit 146 · test-int 54 (pgvector) · contract pass · test-web 10 · test-e2e 14 (S1–S5 doc half) · eval 6 (recall@5 ≥ 0.9) · perf 1 · security 4 · acceptance: pass 22, aws-only 1 · **Overall: PASS** 2026-10-10 15:01 | feat(phase-3) |
| Phase 4 — Pattern intelligence | ☑ done | P4-01 … P4-03 | lint pass · test-unit 157 · test-int 63 · contract pass · test-web 10 · test-e2e 15 (incl. S8) · eval 6 · perf 1 · security 4 · acceptance: pass 25, aws-only 1 · **Overall: PASS** 2026-10-10 15:30 | feat(phase-4) |
| Phase 5 — Model-building agent | ◐ in progress | P5-01 … P5-07 | | |
| Phase 6 — Productionize | ☐ todo | P6-01 … P6-06 | | |
| Phase 7 — Scale | ☐ todo | P7-01 … P7-05 | | |
| Step D — Demo assembly | ☐ todo | S1 … S10 | | |

Status values: ☐ todo · ◐ in progress · ☑ done · ⚠ blocked

## Demo scenarios (BUILD.md §17.2)
| Scenario | Status | Test |
|---|---|---|
| S1 Live routing | ☑ browser pass | web/e2e/demo/s1-*.spec.ts |
| S2 Safety rails | ◐ browser pass for slots / time-sensitive / follow-up; low-confidence step joins with the Phase 5 urgency `dl` artifact (API-level test exists) | web/e2e/demo/s2-*.spec.ts |
| S3 Savings dashboard | ☑ browser pass | web/e2e/demo/s3-*.spec.ts |
| S4 Knowledge answers, shared safely | ☑ browser pass | web/e2e/demo/s4-*.spec.ts |
| S5 Documents change, answers stay correct | ◐ document-change half passes in the browser (incl. lookup demotion); rebuild-from-v2 joins in Phase 5 / Step D | web/e2e/demo/s5-*.spec.ts |
| S6 Pattern → agent → trained model | ☐ | web/e2e/demo/s6-*.spec.ts |
| S7 Lookup from a document | ☐ | web/e2e/demo/s7-*.spec.ts |
| S8 Drift protection | ☑ browser pass (inject/drift-check via API; /demo buttons in Step D) | web/e2e/demo/s8-*.spec.ts |
| S9 Integration (optional) | ☐ | web/e2e/demo/s9-*.spec.ts |
| S10 Real numbers on Bedrock (dry run only in the build) | ☐ | backend/tests/…/test_demo_measure.py |

## Blocked
(none)

## Notes for the next session
- Step 0 reviewed (acceptance-reviewer instructions run via a general-purpose agent, since this session
  was started outside cascade-workspace/). Fixed: frozen baseline migration, Makefile-wide AWS blanking,
  aws-only xfail rule, Playwright --with-deps in CI.
- Phase 1 reviewed; fixed: unit slots + word-order guard (swapped conversions), cached artifact answers
  invalidated on edit/reject/demote, embed cost in net savings, Bedrock inference-profile ids, tests force-blank
  AWS even outside make, low-confidence fallback test.
- Phase 2 reviewed; fixed: `next dev` E2E for P2-01, theme-only colours. Open: S2 low-confidence step in the
  browser (Phase 5).
- Phase 3 reviewed; fixed: compare-and-set version swap (newer uploads win, distinct pending versions), ACL
  recomputed inside the swap, Bedrock KB relevance, context budget on rendered blocks, delete serialized with ingest.
- Phase 4 reviewed; fixed: classifier `none` class no longer contains a pattern's own earlier LLM traffic,
  lookup artifacts drift-sampled, lookup-proposal discovery test.
- Phase 5 (2026-10-10, usage limit reached mid-step): agent, compilers, datasets, integrations, UI, Terraform,
  seed (urgency dl) all written; the last full E2E run passed all 18 browser tests incl. S6/S7/S9/S5-rebuild. The
  Phase 5 acceptance review's 10 gaps are FIXED with regression tests (tests/unit/test_phase5_review.py), and the
  Phase 5 unit suites pass. NOT YET DONE: a final green `make verify` with the acceptance gate at P5
  (scripts/acceptance_gate.txt already says P1..P5), then mark Phase 5 done + commit `feat(phase-5)`. The demo data
  changed (distinct labelled tickets / urgency messages), so the seed version is phase5-v2 and the E2E snapshot rebuilds.
- Next after Phase 5: Phase 6 (Cognito/JWT auth, HTTPS, CloudFront static export, retention job, CI/CD, DEPLOY.md),
  Phase 7 (pgvector cache, rollups, NAT-less artifact subnets, make perf), Step D (/demo page, demo/SCRIPT.md,
  demo-record, whole-build review).
