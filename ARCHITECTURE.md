# Architecture

## Request path

```
prompt ─▶ light layer (in-process, CPU, no LLM call)
           1 normalize & fingerprint   template + typed slots (NUM, UNIT, AMOUNT, EMAIL, ID, DATE, NAME, TEXT…),
                                       volatile / follow-up flags
           [knowledge-scoped: embed ─▶ access-filtered hybrid retrieval ─▶ context fingerprint]
           2 exact cache               sha256(scope | fingerprint | normalized)
           3 embed                     bge-small-en-v1.5 (ONNX, local, 0 tokens)
           4 semantic cache            cosine ≥ 0.95 AND equal slots AND same fingerprint
           5 pattern recognizer        template match / centroid + kNN (trained classifier from Phase 4)
       ─▶ artifact (out of process: persistent local worker, or one Lambda per artifact)
              confidence < min or __FALLBACK__ or error ─▶ falls through
       ─▶ heavy LLM (Bedrock Converse; Demo LLM offline) with budgeted context and cited sources
every request ─▶ exactly one ledger entry (actual tokens/cost, avoided tokens/cost + estimate method)
```

## Data

One PostgreSQL database (Aurora Serverless v2 + pgvector in AWS; `pgvector/pgvector:pg16` locally;
SQLite for the quick start and unit tests), via SQLAlchemy 2 and Alembic:
patterns, users/groups, conversations, cache entries, request log, ledger, overhead, notifications,
collections, documents, chunks (`vector(384)` + HNSW, generated `tsvector` + GIN, `acl text[]` + GIN),
kb_events. The semantic-cache index and exact-key set are kept in memory and rebuilt from the database at
startup (Phase 7 moves the shared index into pgvector).

## Correctness rules (BUILD.md §3) and where they live

| Rule | Mechanism |
|---|---|
| Only verifiable tasks compile | `patterns.compile_pattern` rejects other task types |
| Generated code never in the API process | `runtime.py` (python -I subprocess, scrubbed env) / Lambda; tests/security |
| Two locks before serving | verification ≥ threshold (1.0 for lookups) + human approval + successful deploy |
| Fixed router order, safe fallback | `engine.route` |
| Every request in the ledger, net savings | `engine._finish` (one transaction), `ledger.py` |
| No cross-user leakage | per-user cache scope; global only for PII-free prompts; leak warnings at compile |
| Slots, time, follow-ups | `light/normalize.py`, `light/cache.py` |
| Knowledge never stale or unauthorized | ACL in SQL; fingerprint in cache keys; context ⊆ fingerprinted chunks; `kb_dependencies` + version guard + automatic re-verification (`reverify.py`) |

## Knowledge (RAG)

Upload (presigned S3 / local endpoint) → register (idempotent on sha256; same title = new version)
→ queue (SQS + `cascade-ingest` worker, or an in-process thread) → parse (pypdf, python-docx,
BeautifulSoup, Markdown, CSV) → heading-aware chunks → embed → one-transaction version swap → kb_event
→ re-verification of dependent artifacts. Retrieval: pgvector cosine + Postgres full text fused with RRF
(or SQL-filtered rows scored in memory on SQLite); only chunks near the best match count as relevant;
the cross-encoder reranks them on the LLM path within the context budget.
