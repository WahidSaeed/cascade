# Build progress

Claude Code keeps this file current. Read it first when resuming.

**Current step:** Phase 1 — Foundations (in progress)
**Last `make verify`:** 2026-10-09 19:46 — PASS (Step 0)

| Step | Status | Acceptance IDs | Evidence (last lines of `make verify`) | Commit |
|---|---|---|---|---|
| Step 0 — Foundation | ☑ done | — | lint pass · test-unit 29 passed · test-int 0 tests · contract pass · test-web 1 passed · test-e2e 0 tests · eval/perf/security pass · acceptance: missing 43 (no phase gated yet) · **Overall: PASS** 2026-10-09 19:46 | feat(step-0) |
| Phase 1 — Foundations | ◐ in progress | P1-01 … P1-10 | | |
| Phase 2 — Next.js workspace | ☐ todo | P2-01 … P2-04 | | |
| Phase 3 — Knowledge base & RAG | ☐ todo | P3-01 … P3-08 | | |
| Phase 4 — Pattern intelligence | ☐ todo | P4-01 … P4-03 | | |
| Phase 5 — Model-building agent | ☐ todo | P5-01 … P5-07 | | |
| Phase 6 — Productionize | ☐ todo | P6-01 … P6-06 | | |
| Phase 7 — Scale | ☐ todo | P7-01 … P7-05 | | |
| Step D — Demo assembly | ☐ todo | S1 … S10 | | |

Status values: ☐ todo · ◐ in progress · ☑ done · ⚠ blocked

## Demo scenarios (BUILD.md §17.2)
| Scenario | Status | Test |
|---|---|---|
| S1 Live routing | ☐ | web/e2e/demo/s1-*.spec.ts |
| S2 Safety rails | ☐ | web/e2e/demo/s2-*.spec.ts |
| S3 Savings dashboard | ☐ | web/e2e/demo/s3-*.spec.ts |
| S4 Knowledge answers, shared safely | ☐ | web/e2e/demo/s4-*.spec.ts |
| S5 Documents change, answers stay correct | ☐ | web/e2e/demo/s5-*.spec.ts |
| S6 Pattern → agent → trained model | ☐ | web/e2e/demo/s6-*.spec.ts |
| S7 Lookup from a document | ☐ | web/e2e/demo/s7-*.spec.ts |
| S8 Drift protection | ☐ | web/e2e/demo/s8-*.spec.ts |
| S9 Integration (optional) | ☐ | web/e2e/demo/s9-*.spec.ts |
| S10 Real numbers on Bedrock (dry run only in the build) | ☐ | backend/tests/…/test_demo_measure.py |

## Blocked
(none)

## Notes for the next session
- Step 0 reviewed (acceptance-reviewer instructions run via a general-purpose agent, since this session
  was started outside cascade-workspace/). Fixed: frozen baseline migration, Makefile-wide AWS blanking,
  aws-only xfail rule, Playwright --with-deps in CI.
- Phase 1 WIP: backend/app/light/normalize.py written (not yet tested).
