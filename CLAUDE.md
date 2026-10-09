# Cascade — instructions for Claude Code

BUILD.md is the spec and the source of truth. PROGRESS.md records where the build is.
At the start of every session, and after every compaction, read PROGRESS.md first, then
the BUILD.md sections for the current step.

## How to work
- Follow the build order in BUILD.md §16.3: Step 0, Phases 1–7, then Step D.
- Don't stop between steps to ask questions. When something is unclear, pick the option
  most consistent with BUILD.md, add one line to docs/decisions.md (date, decision, reason),
  and continue.
- A step is done only when its definition of done in §16.3 holds. Paste the last lines of
  `make verify` into PROGRESS.md as evidence; never just assert success.
- After each step:
  1. run the `acceptance-reviewer` subagent on it;
  2. fix the correctness gaps it reports (not style);
  3. update PROGRESS.md;
  4. commit (`feat(step-0): …`, `feat(phase-N): …`).
- Every acceptance ID (P1-01 …) needs a test that names it: `@pytest.mark.acceptance("P3-04")`
  in Python, or `@P3-04` in a Playwright title (BUILD.md §14.3).
- If an item still fails after three focused attempts, record it under "Blocked" in
  PROGRESS.md, continue with the next independent item, and return to it before Step D.
- Use subagents for broad codebase searches, so the main context stays small.

## Never
- IMPORTANT: never call real AWS, run `terraform apply` or `destroy`, use AWS credentials,
  or `git push`. AWS code paths are tested with moto or botocore Stubber. Real-AWS tests
  are marked `aws` and run only through `make test-aws`, which is not part of the build.
- Never delete, skip or weaken a test to make it pass; fix the code. `xfail` is allowed only
  for aws-only behaviour, with the reason and the acceptance ID.
- Never execute generated artifact code inside the API process.
- Never commit secrets.
- Never hand-edit `web/lib/api-types.ts`; regenerate it with `make contract`.

## Commands (created in Step 0)
- `make setup`: install backend (uv) and web (npm) dependencies, Playwright browsers, models
- `make dev`: the local stack via docker compose (Demo LLM, local auth)
- `make lint`, `make test-unit`, `make test-int`, `make test-web`, `make test-e2e`
- `make contract`, `make eval`, `make perf`, `make security`
- `make verify`: the gate. Runs everything except AWS tests and writes reports/
- `make demo-reset`, `make demo-e2e`, `make demo-record`

## Gotchas
- Integration and E2E tests need Docker running (Postgres with pgvector).
- Tests run with `CASCADE_LLM=demo` and `CASCADE_DEMO_LATENCY_MS=0`. Only the live demo uses
  simulated latency.
- The Makefile blanks AWS credentials for every target except `test-aws` and `demo-measure`.
- UI colours, fonts and components come from design/theme/ (BUILD.md Appendix A). No new
  accent colours or fonts.
- Demo data is fictional (Northpeak Outfitters), and all emails use example.com.

## When compacting
Keep the current step and its open acceptance IDs from PROGRESS.md, failing test names with
their errors, and the files changed in the current step.
