# Decisions

One line per decision taken during the build where BUILD.md left a choice open:
date — decision — reason.

- 2026-10-09 — Terraform 1.9.8 installed as a binary in ~/.local/bin (the Makefile adds it to PATH) — Homebrew could not install it (outdated Xcode CLT); only `fmt`/`validate` are used.
- 2026-10-09 — Local models (bge-small, MiniLM cross-encoder, bert-tiny) are cached in `.models/` at the repo root and downloaded by `make models` — no download at test time; the Docker image bakes them in.
- 2026-10-09 — The acceptance gate covers the phases built so far (`scripts/acceptance_gate.txt`); later phases' IDs are reported as `missing` but only fail the gate once their phase begins — §16.3 requires Step 0's `make verify` to run before any feature exists.
- 2026-10-09 — Postgres for tests runs on host port 5433 — avoids clashing with a local Postgres on 5432.
- 2026-10-10 — A semantic cache hit requires equal slots and the same context fingerprint, but not byte-identical template text — §4.1 says "same template and equal slots", yet a reworded prompt (P1-01) always has a different template; proper nouns are slots, so "France" vs "Spain" never share an answer.
- 2026-10-10 — The local embedder runs bge-small through our own onnxruntime session (fastembed only downloads/locates the files) with 2 threads and intra-op spin-waiting off — cuts light-layer p95 roughly in half when the CPU is shared; vectors are identical to fastembed's.
- 2026-10-10 — Exact-cache keys and semantic-cache entry metadata are kept in memory (rebuilt from the database at startup) — cache misses cost no database round trip; Phase 7 moves the shared index to pgvector.
- 2026-10-10 — Artifact answers are cached too (S1 step 3), labelled with the artifact's own estimate method; answers of document-backed artifacts are never cached (rule 13).
- 2026-10-10 — A `lookup` artifact must agree on every fact (verification threshold 1.0) — a single changed value (Germany 30 → 32) must demote it (P3-06), which a 0.9 threshold over ten countries would miss.
- 2026-10-10 — Verification passes only if the artifact answers at least half of the samples itself — an artifact that defers everything would otherwise score 100 %.
- 2026-10-10 — History is sent to the LLM only for context-dependent follow-ups — keeps ordinary requests lean (§4.5).
- 2026-10-10 — The local runtime keeps one persistent worker process per artifact version (python -I, scrubbed env, timeout) — a per-call process spawn would add ~100 ms+ and blow the artifact-route p95 target.
- 2026-10-10 — The demo starting state is built once by replaying the generated week through the real router and snapshotted (SQLite file copy / Postgres template database); `demo-reset` restores the snapshot (< 1 s).
- 2026-10-10 — The docker-compose Postgres runs with synchronous_commit=off — dev only: disk syncs inside Docker Desktop's VM stole host CPU and doubled light-layer p95; Aurora (prod) keeps the default.
- 2026-10-10 — Latency targets are asserted in `make perf` (no coverage tracing), not in the unit suite — coverage instrumentation slows the measured Python stages; same 25 ms threshold, still tagged P1-02.
- 2026-10-10 — Units are a slot type (UNIT, canonicalised: "kilometres" → km), and templates with the same words in a different order never share a semantic hit — from the Phase 1 review: "5 km to miles" must never answer "5 miles to km".
- 2026-10-10 — Cache entries record the artifact that produced them (`cache_entries.pattern_id`); edit, reject and demote delete them (rule 3).
- 2026-10-10 — Net savings subtract router embedding cost (`ledger.embed_cost_usd`) as well as overhead and artifact runtime; baseline stays "actual + avoided" as §4.2 defines it.
- 2026-10-10 — Bedrock model ids default to cross-region inference profiles (`us.anthropic.…`); IAM grants the profiles and their underlying foundation models.
- 2026-10-10 — The urgency `dl` artifact (and the triage artifact) arrive with Phase 5's compilers; until then S2's low-confidence fallback is exercised with a rule artifact that reports low confidence (tests/unit/test_review_regressions.py).
- 2026-10-10 — S2's low-confidence fallback step joins the browser test with Phase 5, when the urgency `dl` artifact it uses exists; until then it is covered at API level (test_review_regressions.py).
- 2026-10-10 — A knowledge prompt's "relevant" chunks are those within 0.06 cosine of the best match and above 0.60; the context fingerprint AND the LLM context use only these — so two users share a cached answer exactly when they would retrieve the same relevant sources (S4), and a cached answer can never cite a chunk outside its fingerprint.
- 2026-10-10 — A re-uploaded document is ingested as a pending version while the current version keeps serving; one transaction inserts the new chunks, flips the version and deletes the old chunks (§9.2 step 6).
- 2026-10-10 — Documents are identified by (collection, title): uploading under an existing title is a new version; the Knowledge view offers "new version" per document.
- 2026-10-10 — `chunks.tsv` is a Postgres generated column owned by the database (not in the ORM); `StrList` maps to `text[]` on Postgres so `acl && principals` uses the GIN index.
- 2026-10-10 — The SQLite path (`InMemoryRetriever`) applies the ACL and collection filters in SQL (json_each) and scores only the permitted rows in memory — rule 13 holds on both backends.
- 2026-10-10 — Every upload gets its own version number and the ingestion swap is a locked compare-and-set (newer upload or another worker wins; stale runs are discarded); the ACL is recomputed inside the swap — from the Phase 3 review.
- 2026-10-10 — Discovery clusters template-first: template groups seed clusters, groups merge at centroid cosine ≥ 0.895 (bge-small: rephrasings of one task 0.90–0.96, a neighbouring task such as routing vs triage ≤ 0.88), remaining prompts join at ≥ 0.80; a cluster is 'covered' by shared templates or by a same-task-type pattern with a near-identical centroid.
- 2026-10-10 — The trained recognizer labels earlier LLM-route traffic by the pattern that would have served it (template or centroid/exemplar match); only the rest is `none` — from the Phase 4 review (otherwise a pattern's own pre-approval traffic teaches it to reject itself).
- 2026-10-10 — Lookup artifacts are drift-sampled too, against the value the LLM extracts from the same retrieved context (in addition to re-verification on document changes).
