# Cascade — Build Specification

**For: Claude Code.** This is the authoritative build plan. It is built in **one unattended
run** (§16): Step 0, then Phases 1–7 in order, then the demo (§17), with tests at every
step (§14). The Design Rules (§3) are invariants that every phase must respect.

> **How to use:** unzip the kit, `cd cascade-workspace`, run `git init` and make a first
> commit, start Claude Code in auto mode (`claude --permission-mode auto`), and paste the
> kickoff goal from §16.2. `CLAUDE.md`, `.claude/settings.json`, the `acceptance-reviewer`
> subagent, `PROGRESS.md` and the theme files in `design/theme/` are already in place.

---

## Contents
1. What we are building
2. Locked decisions
3. Design rules (invariants)
4. Core concepts: light layer, token ledger, artifact kinds, users, knowledge & RAG
5. Tech stack
6. Current repo
7. Phase 1 — Foundations: light layer, users, token ledger, real AWS
8. Phase 2 — Next.js workspace + cost dashboard
9. Phase 3 — Knowledge base & RAG
10. Phase 4 — Pattern intelligence: discovery, task types, drift
11. Phase 5 — The model-building agent (intake, ML/DL, improvement loop)
12. Phase 6 — Productionize
13. Phase 7 — Scale
14. Testing strategy
15. Coding conventions
16. One-go build plan
17. Demo use cases (20 October)
18. Appendix A — Frontend theme & structure

---

## 1. What we are building

Cascade sits between users and an expensive LLM. **Every prompt from every user is
monitored, logged and cached.** When the system recognizes a **recurring pattern** across
prompts, it **compiles that pattern into a deterministic artifact**: plain Python logic, a
classical ML model, or a small deep-learning model, whichever is the cheapest one that
passes verification. Matching prompts are then answered by that artifact instead of the
LLM.

**The purpose is to measure and reduce token consumption.** The product's headline output
is a precise, auditable account of tokens spent, tokens avoided, and money saved, per user,
per pattern, and overall, net of the cost of building and checking the artifacts. The hard
rule: **answer quality must never drop below the LLM's.**

The system has **two models in the request path**:
- **The light model (System 1)** runs on every prompt, in-process and without any LLM call.
  It normalizes the prompt, checks the caches, and recognizes whether the prompt matches a
  known pattern (§4.1).
- **The heavy model (System 2)** is the LLM on Bedrock. It is used only when the light layer
  can't serve the prompt.

Request path: `prompt → light layer (normalize → exact cache → embed → [retrieve, for
knowledge-scoped prompts] → semantic cache → pattern recognizer) → compiled artifact → heavy
LLM (with budgeted context)`. Every step is written to the token ledger.

**Answers can be grounded in documents (RAG).** Users attach knowledge collections
(organization-wide, team or private) to a conversation. Relevant passages are retrieved under
access control, kept to a token budget, and cited in the answer. Retrieved context is usually
most of a request's input tokens, so every cached or compiled answer to a knowledge question
saves the context as well as the question. A cached knowledge answer is reused only when the
same documents, at the same versions, would be retrieved for that user today (§4.5).

**Building the artifact is done by an agent working with the user.** When a pattern is
recognized, the system shows the user the pattern and its projected savings. Before
starting, the **model-building agent** asks the user for:
- any additional instructions,
- any new dataset for training or evaluation,
- any existing system or module the artifact must integrate with.

The agent then builds a rule, ML model or deep-learning model, reports results, and keeps
improving it based on the user's feedback until the user accepts it (or stops the run, or it
hits its budget). Acceptance still has to clear the verification gate before the artifact
serves traffic.

---

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Light layer (System 1) | Runs in-process in the API, CPU only, with no token cost: a prompt normalizer, an exact cache, a **local embedding model** (`BAAI/bge-small-en-v1.5` via **fastembed/ONNX**), a semantic cache, and a **pattern recognizer** (centroid matching first, then a trained logistic-regression classifier). See §4.1. |
| Light LLM (offline only) | A small, cheap Bedrock model (Claude Haiku class; `CASCADE_LIGHT_LLM_MODEL_ID`) used **only** in discovery to name clusters and detect task types. Never on the request path. |
| Heavy model (System 2) | **Amazon Bedrock**, called through the **Converse API** (model-agnostic, returns token usage). Kept behind the `HeavyModel` interface. |
| Backend | **Python 3.12 + FastAPI** (build on the existing repo) |
| Frontend | **Next.js (App Router) + TypeScript + Tailwind**, themed exactly like the Cascade deck (Appendix A) |
| Database | **One PostgreSQL database** for everything: patterns, caches, logs, ledger, agent runs and the knowledge base. **Aurora PostgreSQL Serverless v2 + pgvector** on AWS; the `pgvector/pgvector:pg16` image in docker-compose; SQLite only for the no-Docker quick start and unit tests. Accessed through one `SqlStore` (SQLAlchemy 2 + Alembic). *(Replaces the earlier DynamoDB choice: RAG needs vector search with access filters and keyword search, and the ledger needs SQL aggregation, so a single Postgres is simpler than running DynamoDB alongside a vector store.)* |
| Knowledge base & RAG | Self-built pipeline: ingestion worker (parse → chunk → embed) into pgvector; hybrid retrieval (vector + Postgres full-text) with access filtering in SQL; local cross-encoder reranking; **generation through our own Converse call**, so every context token is in the ledger. Amazon Bedrock Knowledge Bases (`Retrieve` API only) is an optional backend behind the same `Retriever` interface. |
| Artifact execution | subprocess (local dev only) / **AWS Lambda** (prod), one function per approved artifact |
| Model-building agent | Python agent loop using **Bedrock Converse tool use** (no agent framework), running as an **ECS Fargate task** per run; run state persisted in PostgreSQL so it can pause for user input and resume |
| Training jobs | scikit-learn inside the agent task; **SageMaker Training Jobs** for deep learning; all models exported to **ONNX** |
| Integration credentials | **AWS Secrets Manager** |
| IaC / Cloud | **Terraform** on **AWS** |
| Users (v1) | `X-User-Id` header; replaced by Cognito identity in Phase 6 |

---

## 3. Design rules (invariants)

1. **Only verifiable tasks get compiled.** Artifacts are allowed only for task types whose
   output can be checked against the LLM: `classification`, `routing`, `extraction`,
   `transform`, `scoring`, and `lookup` (a fact taken from knowledge documents by slot
   values, e.g. "vacation days in <COUNTRY>"; see §4.5). Open-ended generation (prose,
   summaries, code, free-form answers over documents) is never compiled. Repeated free-form
   knowledge answers are served by the cache instead.
2. **Generated code and models never run in the API process.** In production every artifact
   runs as an isolated Lambda with a least-privilege role and (Phase 7) no network access.
   The local subprocess runtime is a dev convenience, not a security boundary.
3. **Two locks before an artifact serves traffic:** verification score ≥ threshold **and**
   human approval. Any change to an artifact sends it back to `draft` and tears down its
   Lambda.
4. **Fixed router order with safe fallback:** exact cache → semantic cache → recognized
   pattern's artifact → heavy LLM. For knowledge-scoped prompts, candidate retrieval runs
   first, because both cache keys include the context fingerprint (§4.5). An artifact failure,
   low confidence, or `__FALLBACK__` falls through to the LLM; the user never sees an artifact
   error.
5. **Every request writes a ledger entry**, and **every overhead LLM call** (compile, verify,
   label, drift sampling) is recorded as overhead. Savings are always reported **net** of
   overhead. Estimated numbers are labelled as estimates.
6. **No cross-user data leakage.**
   - The cache is scoped **per user by default**. A global (shared) cache entry is allowed
     only for prompts that pass PII screening.
   - Prompts are PII-redacted before they are used for clustering or training data.
   - A compiled artifact must not contain literal user data (names, emails, IDs, free-text
     copied from prompts). The compile step runs a check for this, and the reviewer sees the
     result.
   - Logs carry a retention TTL.
7. **Backends switch by config, not code** (`CASCADE_*` env vars). The `Store`,
   `HeavyModel`, `ArtifactRuntime`, `Ledger`, `Embedder`, `Recognizer` and `Retriever`
   interfaces stay stable.
8. **One design system** — the deck theme in Appendix A. No new accent colours or typefaces.
9. **The agent asks before it spends.** No agent run starts without the intake step (§11.2)
   and an explicit "Start" from the user, who sees the run's budget cap and projected payback
   first. Every run has hard caps (`agent_max_iterations`, `agent_max_cost_usd`); hitting a
   cap pauses the run and asks the user. All agent tokens and training costs are ledger
   overhead.
10. **User satisfaction is required, not sufficient.** "Accept" from the user still goes
    through the verification gate (rule 3). The agent may never mark its own work approved.
11. **Integrations are declared, reviewed and least-privilege.** An artifact may call an
    external system only through an integration the user registered at intake and an admin
    approved: a fixed endpoint or ARN, credentials in Secrets Manager, network egress only to
    that target. Uploaded modules are scanned and shown to the reviewer. An artifact with
    integrations is flagged "not fully deterministic" in the UI, because its output depends
    on the external system.
12. **The light layer is cheap and conservative.** It never calls an LLM on the request
    path. A semantic cache hit also requires the prompt's extracted slots (numbers, dates,
    IDs, names) to match the cached prompt's, so "convert 5 USD" can never return the answer
    to "convert 50 USD". Time-sensitive prompts ("today", "latest", "current price") are not
    cached, or are cached only with a short TTL. Follow-ups that depend on earlier turns in
    the conversation ("what about Germany?") skip the caches and artifacts.
13. **Knowledge answers are never stale or unauthorized.**
    - Retrieval is always access-filtered inside the database query. Results are never
      filtered afterwards in Python.
    - A cached answer to a knowledge-scoped prompt is reused only when its context
      fingerprint matches, i.e. the same chunks at the same versions would be retrieved for
      this user now (§4.5).
    - Artifacts that depend on documents declare them (`kb_dependencies`), are served only to
      users who can read all of them, and are re-verified automatically when those documents
      or their collections change.
    - Generation always goes through Cascade's own Converse call (never an all-in-one
      retrieve-and-generate API), so every context token reaches the ledger.

---

## 4. Core concepts

### 4.1 The light layer (System 1): pattern recognition and caching

The light layer runs on **every** request, inside the API process, on CPU, and makes **no
LLM call**. Target: p95 under 25 ms (under 60 ms for knowledge-scoped prompts, which include
one retrieval query). It lives in `backend/app/light/` and has five stages:

```
prompt
  │ 1. normalize & fingerprint ──▶ normalized text, template, slots, keys, flags
  │ 2. exact cache  (hash lookup) ─────────────────────────────▶ hit → return   [route: cache]
  │ 3. embed  (local small embedding model)
  │ 4. semantic cache  (nearest neighbour + slots must match) ─▶ hit → return   [route: cache]
  │ 5. pattern recognizer ──▶ pattern_id + probability
  │        └─▶ artifact (Lambda) ──▶ result + confidence ──────▶ ok  → return   [route: artifact]
  ▼
heavy LLM (Bedrock), with budgeted context for knowledge-scoped prompts ─▶ return [route: llm]
```

**Knowledge-scoped prompts** (the conversation has collections attached) take one extra step
right after normalizing: embed → **retrieve candidates** (access-filtered, §4.5) → compute
the **context fingerprint**. The exact cache then runs with the fingerprint in its key, and
the semantic cache requires a matching fingerprint. Reranking and context assembly happen
only on the LLM path. **Context-dependent follow-ups** skip stages 2, 4 and 5.

**1. Normalize & fingerprint** (`light/normalize.py`)
- Unicode and whitespace normalization and lowercasing → `normalized`.
- **Slot extraction:** variable parts (numbers, amounts, dates, emails, URLs, IDs, quoted
  strings, and names via the PII detector) are replaced with typed placeholders. This gives
  a `template` (e.g. `what is the sentiment of: <TEXT>`, `convert <NUM> <CURRENCY> to
  <CURRENCY>`) and a `slots` dict.
- Keys: `exact_key = sha256(scope + normalized)`, `template_key = sha256(template)`.
- `volatile = True` if the prompt refers to time ("today", "now", "latest", "current") or
  live data. Volatile prompts skip the cache, or use `volatile_ttl_s`.
- `context_dependent = True` for follow-ups that only make sense with earlier turns: opening
  phrases like "what about", "and for", "how about", "same for", a leading pronoun with no
  subject ("it", "that", "those"), or a very short prompt in an ongoing conversation. These
  skip the caches and artifacts.

**2. Exact cache** (`light/cache.py`) — key-value lookup on `exact_key`, scoped per user or
global. Instant and free, so it runs before embedding.

**3. Embedding model** (`light/embedder.py`) — a **small local model** loaded once at
startup:
- Default: `BAAI/bge-small-en-v1.5` (384 dimensions) via **fastembed** (ONNX runtime, CPU).
  It costs no tokens, so routing doesn't eat into the savings it is meant to produce.
- Options, set by `CASCADE_EMBED_BACKEND`: `multilingual-e5-small` (non-English traffic),
  `bedrock-titan` (pay per token, recorded in the ledger), `hashing` (tests only).
- The model is baked into the Docker image so there is no download at cold start; the
  Fargate task gets 2 GB of memory.
- The same vector is reused as the retrieval query for knowledge-scoped prompts when the
  knowledge base uses the same embedding model (the default), so retrieval adds no embedding
  cost.

**4. Semantic cache** — nearest neighbour among cache entries in scope (the user's own, plus
global entries) with cosine ≥ `cache_threshold`. A hit also requires the **same template and
equal slots** (rule 12). Each entry stores the answer, the original LLM token usage (for the
ledger), `created_at` and a TTL; knowledge-scoped entries also store their context
fingerprint and sources. In v1 this is an in-memory vector index (hnswlib) rebuilt from the
database at startup and updated on write; Phase 7 moves it into pgvector in the same
database.

**5. Pattern recognizer** (`light/recognizer.py`) — decides which approved pattern, if
any, this prompt belongs to. It returns `(pattern_id | None, probability, margin)`:
- **Template match:** if `template_key` equals one of an approved pattern's known templates,
  that is a strong match.
- **Centroid / kNN:** cosine similarity to each approved pattern's centroid and nearest
  stored examples. This is used while there are only a few patterns.
- **Trained light classifier:** once there are at least `recognizer_min_patterns` (e.g. 3)
  approved patterns, a logistic-regression classifier over the embeddings, with classes =
  approved patterns + `none`. It is trained on logged prompts labelled by which pattern
  served them or would have served them, gives calibrated probabilities, is exported to
  ONNX, and is retrained automatically whenever a pattern is approved, demoted or retired.
- A pattern is chosen only if `probability ≥ pattern_min_prob` **and** the margin over the
  runner-up is ≥ `pattern_min_margin`. The artifact's own input guard and confidence make the
  final decision, and anything uncertain goes to the heavy LLM.
- The recognizer sits behind a `Recognizer` interface, so an external decision model (e.g. a
  "System One" model such as Jev) can be plugged in later without touching the router.

**Logging for discovery:** every request stores its embedding, `template_key` and redacted
template, so discovery can cluster on templates and embeddings instead of re-embedding.

**Ledger:** with the default local embedder the light layer costs 0 tokens, and its CPU time
is part of the API's fixed cost. With Titan, `embed_tokens` and their cost are recorded on
every request.

**Light-layer metrics** (exposed via `/api/light/metrics` and shown on the dashboard): exact
and semantic cache hit rates, recognizer match rate, artifact fallback rate, false-hit rate
(found by drift sampling), and p50/p95 latency of each stage.

### 4.2 The token ledger (the product's core metric)

Every request writes one `LedgerEntry`:

| Field | Meaning |
|---|---|
| `request_id`, `user_id`, `ts` | identity and time |
| `route` | `cache` / `artifact` / `llm` |
| `pattern_id`, `artifact_kind` | which artifact served it, if any |
| `model_id` | LLM used (for `llm` route) |
| `input_tokens`, `output_tokens` | **actual** LLM tokens spent (from Bedrock `usage`) |
| `prompt_tokens`, `context_tokens`, `history_tokens`, `system_tokens` | how the input tokens split: the question, retrieved knowledge, earlier turns, instructions (allocated from the measured total, see §9.4) |
| `cache_read_tokens`, `cache_write_tokens` | Bedrock prompt-cache usage, when the model supports it |
| `collection_ids`, `context_fingerprint`, `sources` | the knowledge used for this answer (knowledge-scoped requests) |
| `embed_tokens` | router embedding tokens (routing is not free) |
| `cost_actual_usd` | actual spend for this request (LLM + embeddings + Lambda compute) |
| `tokens_avoided_in`, `tokens_avoided_out` | tokens the LLM *would* have used |
| `cost_avoided_usd` | money that would have been spent |
| `estimate_method` | how "avoided" was computed (see below) |
| `latency_ms` | end-to-end latency |

Overhead calls (compile, verify, labelling, training, drift sampling, document ingestion,
re-verification after document changes) write an `OverheadEntry` with `pattern_id`,
`category`, tokens and cost.

**How "avoided" is estimated** (most to least accurate, always recorded in
`estimate_method`):
- `cache_exact` — a cache entry stores the usage of the original LLM answer; avoided output =
  that entry's output tokens, avoided input = token count of the new prompt **plus the
  entry's context, history and system tokens** (for a knowledge question the context is
  usually the largest part).
- `pattern_measured` — each pattern keeps the average input/output tokens the LLM used on
  that pattern's prompts, measured during verification and drift sampling (where both paths
  actually run).
- `heuristic` — fallback: prompt length / 4 for input, pattern's median output length.

**Derived metrics** (shown in the dashboard):
- *Baseline cost* = actual cost + avoided cost (what everything would have cost on the LLM).
- *Gross saved* = Σ avoided cost. *Net saved* = gross saved − overhead − artifact runtime cost.
- *Savings rate* = net saved / baseline cost.
- *Token reduction* = avoided tokens / (actual + avoided tokens).
- *Per-pattern payback* = cumulative avoided cost vs that pattern's overhead; show
  "paid back after N requests".
- *Input mix* = share of input tokens that are question, context, history and system, plus
  the prompt-cache discount realised.
- All of the above **per user**, **per pattern**, **per knowledge collection**, and **over
  time**.

**Prices:** a `model_prices` table in config (per model: USD per 1M input tokens, per 1M
output tokens, per 1M prompt-cache read and write tokens; embedding price; rerank price if a
Bedrock reranker is used; Lambda GB-second price). Prices are **configured, not hard-coded**;
whoever deploys fills them in from the current AWS pricing pages.

### 4.3 Artifact kinds

All kinds share one runtime contract, so the router does not care what's inside:
Lambda event `{ "prompt": str }` → `{ "result": str, "confidence": float }`. If
`confidence < artifact_min_confidence` or `result == "__FALLBACK__"`, the router falls back
to the LLM.

| Kind | What it is | Training data | Packaging |
|---|---|---|---|
| `rule` | Plain Python logic written by the LLM (parsers, regex, lookup, branching) | a few examples | Lambda zip |
| `ml` | Classical model (TF-IDF or embedding features + logistic regression / gradient boosting) | ≥ `ml_min_samples` (e.g. 200) LLM-labelled prompts | ONNX in a Lambda container image (onnxruntime) |
| `dl` | Small transformer classifier/extractor (e.g. ModernBERT/DistilBERT fine-tune) | ≥ `dl_min_samples` (e.g. 1,000) LLM-labelled prompts | ONNX in a Lambda container image; SageMaker Serverless Inference if it exceeds Lambda limits |

**Escalation ladder (driven by the agent):** the agent tries `rule` first. If verification
fails and enough labelled samples exist, it trains `ml`; if that fails, `dl`. If nothing
passes, the pattern stays on the LLM and is marked "not compilable". Training labels come
from the LLM's own answers on that pattern's prompts (distillation), plus any labelled data
the user uploads at intake. User labels count as ground truth and take precedence in
evaluation. The user can also tell the agent to skip straight to a given kind.

### 4.4 Users

- v1: the client sends `X-User-Id`; the web app has a user switcher so multi-user behaviour
  can be demonstrated.
- Phase 6: identity comes from the Cognito JWT; `X-User-Id` is removed.
- `user_id` is stored on every log, cache entry and ledger entry.
- Each user has **principals** for knowledge access: `user:<id>`, `group:<id>` for each of
  their groups, and `org:*`. In v1 group membership is a `user_groups` table editable in
  Tools; in Phase 6 it comes from the Cognito groups claim.

### 4.5 Knowledge & RAG

Users can attach **knowledge collections** to a conversation and ask questions over those
documents. RAG matters for the token goal, and it needs its own correctness rules.

**Effect on tokens.** In a RAG request most input tokens are retrieved context, usually far
more than the question itself. So:
- Every cache or artifact hit on a knowledge question avoids the whole context, not just the
  question.
- The LLM path is kept lean. Hybrid retrieval is followed by reranking, and only chunks above
  a relevance threshold are sent, capped at `rag_max_context_tokens`. If nothing is relevant,
  no context is sent at all.
- The stable part of the prompt (system and grounding instructions) comes first, so Bedrock
  prompt caching can discount it on models that support it.
- The ledger splits input tokens into question, context, history and system, and records
  prompt-cache reads and writes, so each of these effects can be measured.

**Correctness.** A cached answer is only right while its source documents are unchanged and
the user is allowed to see them.
- Retrieval is filtered by the user's principals inside the SQL query.
- For knowledge-scoped prompts, candidate retrieval runs **before** the cache. It costs no
  tokens, because it reuses the light layer's embedding. Its result is reduced to a **context
  fingerprint**: a hash of the IDs and versions of the top candidate chunks. A cache hit
  requires the same fingerprint, so an answer is reused only if the same documents, at the
  same versions, would be retrieved for this user today. This has three consequences:
  - Changed, added or removed documents stop old entries from matching, with no explicit
    invalidation step.
  - Two users share a cached answer only if both can retrieve exactly the same sources.
  - Organisation-wide FAQs (the most repetitive knowledge traffic) are safely shared across
    users.
- Artifacts built from documents, such as the `lookup` task type (a slot value mapped to a
  fact from a document table), declare their `kb_dependencies`. The router serves them only
  to users who can read all of them. Any change to those documents, or a new document in
  their collection, triggers automatic re-verification.

**Follow-up questions.** In a chat, "what about Germany?" depends on the previous turn. The
normalizer flags such prompts as `context_dependent`. They skip the caches and artifacts,
retrieval uses the previous question plus the current one, and the LLM receives recent turns
within `history_max_tokens` (counted as `history_tokens`).

---

## 5. Tech stack

**Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic v2 + pydantic-settings, NumPy, boto3.
**Light layer (System 1):** fastembed + ONNX Runtime running `BAAI/bge-small-en-v1.5`
(local embeddings, CPU); hnswlib (in-memory vector index for the semantic cache and pattern
kNN); scikit-learn logistic regression exported to ONNX (the pattern classifier). Optional:
Bedrock Titan Text Embeddings v2.
**Heavy model (System 2):** Amazon Bedrock (Converse API). A small Bedrock model (Haiku
class) is used offline for discovery only. A mock LLM is used for offline dev and tests.
**ML / DL:** scikit-learn, skl2onnx, onnxruntime; PyTorch + Hugging Face Transformers
(optionally PEFT/LoRA) for `dl`; Amazon SageMaker Training Jobs.
**Privacy:** Amazon Comprehend `DetectPiiEntities` (or Microsoft Presidio locally) for PII
screening/redaction.
**Data:** PostgreSQL with pgvector (Aurora PostgreSQL Serverless v2 on AWS;
`pgvector/pgvector:pg16` in docker-compose) for everything, via SQLAlchemy 2 + psycopg 3,
with Alembic migrations; SQLite for the no-Docker quick start and unit tests; S3 (documents,
artifacts, training datasets, model files). Phase 7: ElastiCache (Redis) for the hot cache.
**Knowledge / RAG:** parsers pypdf (PDF), python-docx (DOCX), BeautifulSoup (HTML),
Markdown/plain text/CSV, with Docling optional for layout-heavy PDFs; a heading-aware chunker;
pgvector (HNSW) + Postgres full-text search, fused with reciprocal rank fusion; a fastembed
cross-encoder reranker (`Xenova/ms-marco-MiniLM-L-6-v2` by default, `BAAI/bge-reranker-base`
optional) or a Bedrock rerank model; Amazon SQS + an ECS Fargate ingestion worker; optional
Amazon Bedrock Knowledge Bases backend (`Retrieve` API only).
**Execution:** AWS Lambda (zip for `rule`; container images from ECR for `ml`/`dl`).
**Agent:** Bedrock Converse API with tool use (Claude on Bedrock); runs as ECS Fargate tasks;
state in PostgreSQL; live progress to the UI via Server-Sent Events.
**Integrations:** AWS Secrets Manager (credentials); S3 presigned uploads (datasets,
modules).
**Scheduling / notifications:** Amazon EventBridge (discovery and drift schedules); in-app
notifications, plus optional email via Amazon SES.
**Frontend:** Next.js (App Router), React, TypeScript, Tailwind CSS, next/font (Inter,
JetBrains Mono).
**Infrastructure:** Terraform; Amazon ECR, ECS on Fargate, Application Load Balancer, IAM,
CloudWatch, S3 + CloudFront (web), Cognito + ACM (Phase 6).
**DevOps:** Docker, docker-compose, AWS CLI, pytest, Playwright, GitHub Actions.

---

## 6. Current repo

```
cascade-workspace/
├── BUILD.md                  # this spec
├── CLAUDE.md                 # standing instructions for Claude Code
├── PROGRESS.md               # build progress (Claude Code keeps it current)
├── .claude/settings.json     # pre-approved build commands; blocks push, apply, AWS CLI
├── .claude/agents/acceptance-reviewer.md   # reviews each step against this spec
├── design/theme/             # tailwind.config.ts + globals.css (Appendix A)
├── ARCHITECTURE.md, README.md, docker-compose.yml
├── backend/app/
│   ├── config.py      # Settings (CASCADE_* env vars)
│   ├── schemas.py     # Pydantic models
│   ├── embeddings.py  # local embedder + Bedrock Titan
│   ├── heavy.py       # HeavyModel: Mock / Bedrock
│   ├── sandbox.py     # run_handler: subprocess / lambda
│   ├── store.py       # Store: SQLite / DynamoDB
│   ├── patterns.py    # compile_handler() — rule artifacts only
│   ├── verifier.py    # verify() — exact-match agreement
│   ├── engine.py      # route() — cache → pattern → heavy
│   ├── main.py        # FastAPI endpoints
│   └── seed.py        # demo data
├── frontend/index.html   # vanilla UI — REFERENCE ONLY, replaced by web/ in Phase 2
├── infra/terraform/      # ECR, DynamoDB, S3, IAM, ECS/Fargate, ALB
└── scripts/push.sh
```
Working and tested: routing, compile → verify → approve → route for `rule` artifacts,
SQLite store, mock LLM, subprocess runtime, basic metrics. **Missing:** a real light layer
(the current `embeddings.py` hashing embedder is a placeholder, not a model, and there's no
normalizer, exact cache, slot check or recognizer), users, token accounting, the move to
PostgreSQL, Lambda deployment, the knowledge base and RAG, ML/DL artifacts, discovery,
drift, Next.js UI.

---

## 7. Phase 1 — Foundations: light layer, users, token ledger, real AWS

**Goal:** a real light layer, every request attributed to a user and fully costed, and the
system running on real Bedrock, PostgreSQL and Lambda.

### 7.1 The light layer (implements §4.1)
Create `backend/app/light/` and make `engine.route()` call it:
- `normalize.py` — `normalize(prompt, conversation) -> Normalized{text, template, slots,
  exact_key, template_key, volatile, context_dependent}`. Ship a test table for the slot
  extractor (numbers, currencies, dates, emails, URLs, IDs, quoted text) and for follow-up
  detection.
- `cache.py` — `ExactCache` (key-value on `exact_key`, scoped) and `SemanticCache` (hnswlib
  index, cosine threshold, **template + slot equality**, TTL, volatile handling), both
  persisted through the `Store` and rebuilt at startup.
- `embedder.py` — `Embedder` interface; `FastEmbedEmbedder` (default, bge-small-en-v1.5),
  `TitanEmbedder`, `HashingEmbedder` (tests). Returns `(vector, token_count)`.
- `recognizer.py` — `Recognizer` interface; `CentroidRecognizer` (template match +
  centroid/kNN) for v1. The trained classifier comes in Phase 4. Returns
  `(pattern_id|None, prob, margin)`.
- Replace the old `embeddings.py` usage across the code; keep its cosine helper.
- Re-tune the thresholds for bge-small (cache ≈ 0.95, pattern ≈ 0.80 as starting points),
  check them against a labelled sample set, and record the values in `.env.example`.
- Dockerfile: download the fastembed model at build time; ECS task memory → 2048 MB.
- `GET /api/light/metrics` — hit and match rates, per-stage latency.
- Each response includes `light: {stage_hit, template, prob, latency_ms_by_stage}` so the UI
  can show why it was routed that way.

### 7.2 Users
- Read `X-User-Id` in `POST /api/chat` (default `"anonymous"`); thread `user_id` through
  `engine.route()`, the cache, logs and ledger.
- Cache lookups filter by `user_id` unless the entry is `scope="global"` (see 7.5).
- Conversations: `POST /api/chat` takes an optional `conversation_id`; store conversations
  and their turns. This is needed for follow-up detection now and for history in Phase 3.

### 7.3 LLM usage capture
- Switch `BedrockHeavyModel` to the **Converse API**; return `(text, usage)` where usage =
  `input_tokens`, `output_tokens`.
- Replace `MockHeavyModel` with the **Demo LLM** (§17.1). It gives scenario-aware answers,
  realistic token usage computed from the actual assembled prompt, and simulated latency
  (zero in tests), so the cost dashboard and the 20 October demo show meaningful numbers
  offline.
- Embedding calls return their token count (`inputTextTokenCount` from Titan; length/4
  locally).

### 7.4 The ledger
- New `app/ledger.py` with `Ledger.record(entry)`, `Ledger.record_overhead(entry)` and
  aggregate queries, stored in two new tables (`ledger`, `overhead`) in `SqlStore`. The
  aggregates are plain SQL `GROUP BY` queries (by user, pattern, collection, time bucket).
- `app/pricing.py`: loads `model_prices` from config; `cost(model_id, in_tok, out_tok)`.
- `engine.route()` writes exactly one `LedgerEntry` per request, using the estimation
  methods in §4.2. Cache entries store the original answer's usage.
- Patterns gain `avg_input_tokens`, `avg_output_tokens`, `samples_measured`, updated by
  verification.
- Compile and verify write `OverheadEntry` rows.
- New endpoints: `GET /api/ledger/summary?from&to&user_id&pattern_id`,
  `GET /api/ledger/timeseries?bucket=hour|day`, `GET /api/ledger/by-user`,
  `GET /api/ledger/by-pattern` (includes payback).

### 7.5 PII screening (minimal v1)
- `app/privacy.py`: `contains_pii(text) -> bool` and `redact(text) -> str`; regex-based
  locally (emails, phones, card numbers, IDs), Comprehend in AWS (config switch).
- Cache writes are `scope="user"` by default; `scope="global"` only when the prompt passes
  `contains_pii == False` and `CASCADE_GLOBAL_CACHE=true`.
- Compile step: scan generated code for string literals that appear verbatim in logged
  prompts; attach findings to the pattern as `leak_warnings` for the reviewer.

### 7.6 Real AWS
- **One database.** Replace `SqliteStore` and `DynamoStore` with a single `SqlStore`
  (SQLAlchemy 2 + psycopg 3, Alembic migrations) that runs on SQLite (no-Docker quick start,
  unit tests) and PostgreSQL (docker-compose and AWS). Add a `postgres` service
  (`pgvector/pgvector:pg16`) to docker-compose and make it the default local setup. Enable
  the `vector` extension in the first migration so Phase 3 can use it.
- Terraform: remove the DynamoDB tables; add an **Aurora PostgreSQL Serverless v2** cluster
  in private subnets, with credentials in Secrets Manager and a security group that admits
  only the ECS tasks. Pass connection settings to the tasks through env vars and secrets.
- **Lambda deploy-on-approve** — new `app/deploy.py`:
  - `deploy(pattern)` packages `rule` code into a zip whose `lambda_function.lambda_handler`
    execs the code, calls `handle(prompt)`, returns `{result, confidence: 1.0}`; creates or
    updates `cascade-artifact-{pattern_id}` (python3.12, 5 s, 256 MB, role
    `CASCADE_ARTIFACT_ROLE_ARN`); also writes the code to the versioned S3 bucket.
  - `teardown(pattern_id)` deletes the function, ignoring NotFound.
  - Wire it in: approve → deploy (set `approved` only if deploy succeeds); edit/reject of an
    approved pattern → teardown.
- Terraform: artifact execution role (`AWSLambdaBasicExecutionRole` only); grant the ECS task
  role `lambda:Create/Update/Delete/GetFunction` on `cascade-artifact-*` plus
  `iam:PassRole` on that role; pass the role ARN to the task; ensure `terraform validate`
  passes.

### Phase 1 acceptance
- [ ] **P1-01** The light layer runs locally with the bge-small model: a repeated prompt is an exact
      cache hit; a reworded prompt is a semantic cache hit; "convert 5 USD to EUR" does
      **not** hit a cached "convert 50 USD to EUR"; "what's the price today" is not cached.
- [ ] **P1-02** An approved pattern is recognized for new prompts in its shape, and an off-pattern
      prompt goes to the LLM; light-layer p95 is under 25 ms on the dev machine.
- [ ] **P1-03** Two different `X-User-Id`s get separate cache entries; global caching only for
      PII-free prompts when enabled.
- [ ] **P1-04** Every chat request produces one ledger entry with correct route, actual tokens/cost,
      avoided tokens/cost and `estimate_method`.
- [ ] **P1-05** Compile and verify produce overhead entries; `/api/ledger/summary` reports gross
      saved, overhead, **net saved** and savings rate.
- [ ] **P1-06** Offline (mock) mode produces realistic token numbers.
- [ ] **P1-07** The same test suite passes against SQLite and against PostgreSQL (docker-compose);
      Alembic migrations run from empty on both.
- [ ] **P1-08** A follow-up such as "what about Germany?" in an existing conversation is flagged
      `context_dependent` and skips the caches.
- [ ] **P1-09** Approving deploys `cascade-artifact-<id>` through the Lambda API, and editing or
      rejecting tears it down (moto during the build, real Lambda in AWS); matching prompts
      are then served by it.
- [ ] **P1-10** With `CASCADE_LLM=bedrock`, non-matching prompts get real Bedrock answers with real
      usage (aws-only).

---

## 8. Phase 2 — Next.js workspace + cost dashboard

**Goal:** the product UI in `web/`, with **token cost as the headline view**. Theme and
structure are in Appendix A.

Views:
1. **Console** — chat with a user switcher (sets `X-User-Id`). Each answer shows a route
   badge (`cache` / `artifact` / `llm`), latency, and **tokens spent or tokens saved** for
   that message (e.g. "LLM · 1,240 tokens · $0.0062" or "Artifact · saved ~1,180 tokens").
2. **Savings (the dashboard, default landing view after the console)** — stat tiles: total
   tokens spent, tokens avoided, token reduction %, net $ saved, savings rate; a time series
   of baseline vs actual cost; route mix; per-user table; per-pattern table with payback.
   Clearly mark estimated figures.
3. **Patterns** — library grid and review drawer: artifact kind, code (for `rule`) or model
   card (for `ml`/`dl`: features, training size, metrics), verification result with
   disagreements, measured avg tokens, projected monthly savings, `leak_warnings`, and
   approve / edit / reject.
4. **Tools** — configuration: LLM model, embeddings, thresholds, artifact runtime, approval
   threshold, **model prices**, global-cache toggle, ML/DL sample minimums.

Charts: a small, typed charting setup (Recharts or similar), styled with the deck tokens.

### Phase 2 acceptance
- [ ] **P2-01** `web/` runs with `next dev` against the local API; all views match the deck theme.
- [ ] **P2-02** Switching users in the console changes cache behaviour and per-user ledger numbers.
- [ ] **P2-03** The Savings view reconciles: its totals equal `/api/ledger/summary`.
- [ ] **P2-04** `next build` with `output: "export"` produces a working static `out/`.

---

## 9. Phase 3 — Knowledge base & RAG

**Goal:** users can ask questions over documents and get cited answers. Those answers must
respect document access, stay within a token budget, be fully costed, and be safe to cache
(§4.5, rule 13).

### 9.1 Storage
All tables live in PostgreSQL (pgvector enabled) and are created by Alembic migrations:
- `collections(id, name, owner_id, access: org|group|private, group_ids[])`
- `documents(id, collection_id, title, source_uri, mime, version, sha256, status:
  queued|parsing|embedding|ready|failed, error, created_by, updated_at)`
- `chunks(id, document_id, doc_version, ord, text, heading_path, page, token_estimate,
  embedding vector(384), tsv tsvector, acl text[])`, with an HNSW index on `embedding` and
  GIN indexes on `tsv` and `acl`.
- `kb_events(id, collection_id, document_id, kind: added|updated|deleted, at)`, which drives
  artifact re-verification.

`acl` lists the principals allowed to read a chunk (`org:*`, `group:<id>`, `user:<id>`). It
is copied from the collection and rewritten whenever the collection's access changes.

### 9.2 Ingestion (`app/rag/ingest.py`, worker `cascade-ingest`)
1. The client uploads via an S3 presigned URL to `kb/<collection>/<document>/<version>/` and
   registers the document (`queued`).
2. An SQS message triggers the worker. Locally this is an in-process background task.
3. **Parse:** PDF (pypdf), DOCX (python-docx), HTML (BeautifulSoup), Markdown and plain text,
   CSV (each row becomes a record). Docling is an optional parser for layout-heavy PDFs with
   tables.
4. **Chunk:** split on headings first, then by size (`chunk_tokens` ≈ 500,
   `chunk_overlap` ≈ 60). Keep each chunk's heading path, page number and a token estimate.
5. **Embed** with the knowledge embedding model. The default is the same bge-small model as
   the light layer, so query vectors can be reused.
6. **Swap versions atomically:** insert the new version's chunks, mark the document `ready`,
   delete the old version's chunks, and write a `kb_event`.

Ingestion is idempotent on `(document_id, sha256)`. Bedrock tokens are used during ingestion
only if Bedrock parsing or embeddings are configured; when they are, they are recorded as
`ingestion` overhead. v1 sources are file uploads and S3 prefixes. Connectors (SharePoint,
Confluence, Google Drive, web crawl) come later, or are available now through the Bedrock
Knowledge Bases backend.

### 9.3 Retrieval (`app/rag/retriever.py`)
The `Retriever` interface has three methods:
- `candidates(query_vec, query_text, principals, collection_ids, k)` runs a hybrid search:
  pgvector cosine top-k plus Postgres full-text top-k, fused with reciprocal rank fusion. The
  access filter (`acl && :principals`) and the collection filter are part of the SQL. Use
  pgvector 0.8 or later with iterative index scans enabled (`hnsw.iterative_scan`), so that
  a user who can see only a small part of the knowledge base still gets a full top-k.
- `fingerprint(candidates)` returns the sha256 of the sorted `(chunk_id, doc_version)` pairs
  of the top `rag_fingerprint_k` (e.g. 8) fused candidates.
- `build_context(candidates, query_text)` runs on the LLM path only:
  - A cross-encoder reranks `rag_candidates` (e.g. 30) chunks.
  - Chunks scoring at least `rag_rerank_min` are kept, up to `rag_top_k` (e.g. 5) chunks and
    `rag_max_context_tokens` (e.g. 3,000) tokens.
  - Adjacent chunks from the same document are merged, and near-duplicates are dropped.
  - If no chunk passes, the context is empty and `rag_no_context` decides the behaviour:
    `answer_without_context` or `say_not_found`.

Backends:
- `PgVectorRetriever` is the default, for docker-compose and AWS.
- `InMemoryRetriever` (hnswlib + rank-bm25) is for tests and the no-Docker quick start.
- `BedrockKBRetriever` is optional. It uses the Bedrock Knowledge Bases `Retrieve` API with
  metadata filters for access. It never uses `RetrieveAndGenerate`, so generation and its
  tokens stay in Cascade's ledger.

The default reranker is the fastembed cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` (fast on
CPU). Alternatives are `BAAI/bge-reranker-base` (better but slower) or a Bedrock rerank model
(paid per query and recorded in the ledger).

### 9.4 Generation (`app/rag/generate.py`)
- The Converse request is built in this order:
  1. system prompt plus grounding and citation instructions (stable, and marked as a
     prompt-cache point when the model supports it);
  2. the numbered context chunks with their titles;
  3. recent turns, up to `history_max_tokens`;
  4. the question.
- The model cites `[n]`. The response returns `answer` and `sources[] {document_id, title,
  page, chunk_id, snippet}`. Answers without citations are flagged in the ledger entry
  (`uncited=true`) for review.
- Usage: record input, output, and prompt-cache read/write tokens from Converse. Split the
  measured input total across system, context, history and prompt in proportion to their
  estimated sizes, recorded as `split_method="proportional"`.

### 9.5 Cache, artifacts and conversations (implements §4.5)
- For knowledge-scoped requests, both cache keys include the context fingerprint. Cache
  entries store `sources`, the fingerprint, and the original usage including context tokens,
  so `cache_exact` savings count the context.
- A cached answer is reused across users when fingerprints match and the prompt passes PII
  screening. There is no explicit invalidation: stale entries stop matching and expire by
  TTL.
- Artifacts gain `kb_dependencies` (document IDs and versions), and the router checks that
  the user can read all of them. A `kb_event` on a dependency, or a new document in its
  collection, queues re-verification against the RAG pipeline on the artifact's holdout set.
  If agreement falls below the threshold, the artifact is demoted to `shadow` and the UI
  shows an alert. This cost is recorded as `reverify` overhead.
- Follow-ups flagged `context_dependent` skip the caches and artifacts. Their retrieval query
  is the previous user question plus the current one.

### 9.6 API
- Collections: `POST /api/collections`, `GET /api/collections`, and
  `PATCH /api/collections/{id}` (name, access).
- Documents: `POST /api/documents/upload-url`, `POST /api/documents`,
  `GET /api/documents?collection_id=`, `DELETE /api/documents/{id}`, and
  `POST /api/documents/{id}/reindex`.
- `POST /api/search` runs retrieval only, for debugging. It returns the candidates, their
  scores, the fingerprint, and the context that would be sent with its token count.
- `POST /api/chat` gains `collection_ids[]`; the response gains `sources[]` and the token
  split.
- `GET /api/rag/metrics` reports retrieval latency, relevance-gate rate, average context
  tokens, knowledge cache hit rate and citation rate.
- The ledger endpoints gain the question/context/history/system split and a per-collection
  breakdown.

### 9.7 UI (`web/`)
- **Knowledge** view (`/knowledge`):
  - collections with access badges;
  - drag-and-drop upload and ingestion status;
  - documents with their versions, and a chunk preview;
  - re-index and delete actions;
  - token totals per collection.
- **Console:**
  - a collection picker for each conversation;
  - citation chips (title, page) under each answer, which open the source snippet;
  - a token note showing context tokens against question tokens.
- **Savings:** the input mix (how much of input is context), the knowledge cache hit rate, and
  tokens avoided on knowledge questions.

### 9.8 Infrastructure
Terraform adds:
- the S3 prefix `kb/`;
- an SQS queue with a dead-letter queue;
- the ECS service `cascade-ingest`, scaling on queue depth with a minimum of 0;
- IAM permissions so the API, agent and ingest worker can reach the database, `kb/` and the
  queue.

The database is the Aurora cluster from Phase 1, with the `vector` extension enabled. Locally
it is the `pgvector/pgvector:pg16` service in docker-compose.

### Phase 3 acceptance
- [ ] **P3-01** Uploading a PDF, a DOCX and a Markdown file produces `ready` documents with chunks.
      Re-uploading a changed file creates a new version and removes the old chunks.
- [ ] **P3-02** A user without access to a private collection never sees its chunks in `/api/search`
      results or citations (tested at the SQL level).
- [ ] **P3-03** Answers cite their sources, and context never exceeds `rag_max_context_tokens`. An
      unrelated question sends no context.
- [ ] **P3-04** Two users with the same access ask the same question: the second gets a cache hit. A
      user without access to a source document gets a miss.
- [ ] **P3-05** After the cited document is updated, the same question misses the cache and returns
      the new answer.
- [ ] **P3-06** A rule artifact that declares a document dependency is re-verified, and demoted, when
      that document changes in a way that changes its answers.
- [ ] **P3-07** The ledger shows the question/context/history split, and prompt-cache tokens when
      caching is active. Avoided cost on knowledge cache hits includes the context tokens.
- [ ] **P3-08** "What about Germany?" after a question about France skips the cache and is answered
      using the conversation history.

---

## 10. Phase 4 — Pattern intelligence

**Goal:** the system finds patterns itself, verifies them correctly per task type, and
protects quality over time.

- **Task types:** add `task_type`
  (`classification|routing|extraction|transform|scoring|lookup`) to patterns. The compile
  endpoint requires it and rejects generation tasks. `lookup` patterns must also declare
  `kb_dependencies`.
- **Per-type verification** (`verifier.py`). Record measured LLM token usage while
  verifying.
  - classification and routing: normalized exact match;
  - extraction: JSON field-level agreement;
  - transform: normalized string equality;
  - scoring: numeric agreement within a tolerance;
  - lookup: the artifact's value must match the value the heavy LLM extracts, as structured
    output, from the same retrieved context.
- **Discovery** (`app/discovery.py`): take recent `llm`-route prompts (PII-redacted), embed,
  cluster (greedy cosine clustering, min size `discovery_min`), skip clusters near existing
  patterns, and ask the LLM to classify the cluster's task type. For eligible types only,
  create a pattern with status `recognized` and rank it by **projected token savings**
  (cluster frequency × measured tokens per request). Each new recognized pattern sends the
  user a notification that opens the agent intake (Phase 5). Triggered via
  `POST /api/patterns/discover` and daily via EventBridge. Knowledge-scoped prompts take
  part in discovery too. A cluster whose prompts are free-form questions over the same
  sources is left to the shared cache. A cluster that varies only by slot values over the
  same documents (e.g. "vacation days in <COUNTRY>") is proposed as a `lookup` pattern.
- **Trained light classifier** (`light/recognizer.py::ClassifierRecognizer`): once
  `recognizer_min_patterns` patterns are approved, train the logistic-regression recognizer
  (approved patterns + `none`) on logged embeddings, export to ONNX, hot-swap it into the
  API, and retrain on every approve/demote. Keep `CentroidRecognizer` as the fallback.
- **Light LLM in discovery:** cluster naming, task-type detection and cluster-coherence
  checks use the small Bedrock model (`CASCADE_LIGHT_LLM_MODEL_ID`), recorded as overhead.
  Clustering runs on the stored embeddings and templates.
- **Drift** (`app/monitor.py`): send `drift_sample_rate` (e.g. 5 %) of artifact traffic to
  the LLM as well, log agreement (as overhead), compute rolling agreement per pattern, and
  auto-demote below `drift_min_agreement` with a UI alert.

### Phase 4 acceptance
- [ ] **P4-01** Replaying a burst of similar prompts then running discovery produces a ranked draft
      with projected savings.
- [ ] **P4-02** Extraction verification compares JSON correctly; generation tasks cannot be compiled.
- [ ] **P4-03** A deliberately broken artifact is auto-demoted by the next drift run; drift cost shows
      up as overhead.

---

## 11. Phase 5 — The model-building agent

**Goal:** when a pattern is recognized, an agent builds the artifact (rule, ML or deep
learning) together with the user. It first collects the user's instructions, data and
integrations, then iterates until the user is satisfied.

### 11.1 Pattern & run lifecycle

```
recognized ──(user opens intake)──▶ awaiting_intake ──(Start)──▶ agent_running
     ▲                                                              │  ▲
     │                                          iteration report ───┘  │ feedback / new data
     │                                                  ▼               │
     │                                         awaiting_feedback ───────┘
     │                                                  │ Accept
     │                                                  ▼
     │                         verifying (rule 3 gate) ──fail──▶ awaiting_feedback
     │                                                  │ pass
     │                                                  ▼
     │                                        draft → approved (deployed)
     └──── stopped / budget_exhausted / not_compilable (pattern stays on the LLM)
```
A pattern can have several runs over time. For example, "Improve" on an approved artifact
starts a new run, and the old version keeps serving until the new one is approved.

### 11.2 Intake: the agent asks before it starts

When the user opens a recognized pattern, the UI shows a summary: example prompts
(redacted), frequency, measured tokens per request, projected monthly savings, the task type
the system detected, and a **suggested plan** from the agent (e.g. "try rules first; 1,400
labelled examples are available, enough for ML"). It then asks three questions, each of
which can be skipped:

1. **Additional instructions** — free text: labels/classes to use, edge cases, accuracy vs
   latency priority, kinds to try or avoid ("go straight to a deep-learning model").
2. **New dataset** — upload CSV/JSONL/Parquet via S3 presigned URL. The user maps columns
   (`input`, optional `label`/`expected_output`) and chooses its use: *train*, *evaluate*, or
   *both*. On upload: schema validation, row count, label distribution, PII scan (with
   redaction or rejection), and deduplication against logged prompts. The user can also
   attach **knowledge collections** the agent may search and extract facts from. These are
   needed for `lookup` artifacts, and the agent only sees documents the user can read.
3. **Integrations with existing systems/modules** — register zero or more:
   - **HTTP endpoint** (an existing model or internal API): URL, method, request/response
     mapping, auth secret (stored in Secrets Manager).
   - **AWS resource**: an existing SageMaker endpoint or Lambda, by ARN.
   - **Python module**: upload a wheel or zip to include in the artifact; it is
     dependency-scanned and shown to the reviewer.
   - For each, the user states its role: *feature source* (e.g. a customer-tier lookup),
     *pre/post-processing step*, or *core model* (wrap an existing model instead of
     training).

   Integrations need admin approval before the agent can call them (rule 11).

Before "Start", the user sees the run's **budget** (max iterations, max USD, both
defaulting from config and editable), the cost estimate for the first iteration, and the
payback projection. Nothing runs until the user clicks **Start**. If the user ignores
the intake, the pattern stays `awaiting_intake`; there is no auto-start unless
`agent_autostart=true`, in which case defaults are used and the user is notified.

### 11.3 The agent loop

A Python loop (`app/agent/runner.py`) using the **Bedrock Converse API with tool use**,
running as an ECS Fargate task (`cascade-agent`). The run state (`AgentRun`) is saved to
PostgreSQL after every step, so the task can stop while waiting for the user and a new task
can resume the run later.

Each **iteration**:
1. Plan, using the intake answers, previous results and user feedback.
2. Build or refresh the dataset (LLM-labelled logs + user data; user labels take
   precedence).
3. Produce a candidate: write a `rule`, train an `ml` model, or launch a `dl` SageMaker job,
   optionally wiring in registered integrations.
4. Evaluate on the holdout set: agreement with the LLM, accuracy on user-labelled data if
   any, per-class errors, latency, model size, cost per request, projected net savings.
5. Write an **iteration report** and set the run to `awaiting_feedback`.

The user then chooses one of:
- **Accept** — goes to the verification gate (rule 3) and then the normal approve/deploy
  flow.
- **Keep improving** — with feedback text; the agent starts the next iteration.
- **Add data / integration** — reopens part of the intake, then continues.
- **Stop** — ends the run; the pattern stays on the LLM.

Caps: `agent_max_iterations` (default 5) and `agent_max_cost_usd` (default set in config).
On reaching a cap the run pauses with a report and asks whether to raise the budget or stop.

**Agent tools** (`app/agent/tools.py`, each a typed function exposed to the model):

| Tool | Does |
|---|---|
| `get_pattern_summary` | pattern stats, examples (redacted), measured tokens |
| `build_dataset(sources, split)` | merge logs + user datasets → train/val/holdout in S3 |
| `label_with_llm(n)` | extra LLM labelling, recorded as overhead, budget-checked |
| `write_rule(code)` | save a `rule` candidate (literal-data leak check runs automatically) |
| `train_ml(features, model, params)` | scikit-learn training → ONNX |
| `train_dl(base_model, task, params)` | start a SageMaker Training Job → ONNX; poll status |
| `call_integration(id, payload)` | call an approved integration only |
| `search_knowledge(query, collection_ids)` | access-filtered retrieval over the attached collections, as the run's user |
| `extract_table(document_ids, schema)` | LLM extracts a structured table from documents for a `lookup` artifact (overhead); records `kb_dependencies` with document versions |
| `evaluate(candidate_id)` | holdout metrics vs LLM and user labels, latency, size, cost |
| `inspect_errors(candidate_id, k)` | show the k worst disagreements |
| `report(summary, recommendation)` | write the iteration report, pause for the user |
| `ask_user(question)` | pause with a specific question (e.g. "Is 'refund' a separate class?") |

The agent cannot approve, deploy, change config, call unregistered endpoints, or exceed its
budget. Tools enforce all of this, not the prompt.

**Compilers** behind the tools:
- `app/compilers/ml.py` — TF-IDF or router-embedding features; logistic regression or
  gradient boosting; skl2onnx export; calibrated confidence.
- `app/compilers/dl.py` — SageMaker Training Job fine-tuning a small transformer
  (ModernBERT/DistilBERT; sequence classification or token tagging for extraction); ONNX
  export to S3.
- **Packaging:** generic Lambda container images (`ml-runtime`, `dl-runtime`, onnxruntime)
  load their model from S3 at cold start. Artifacts with integrations get their adapter code
  bundled, and network egress only to the declared targets.

### 11.4 Data model
- `AgentRun`: `id, pattern_id, user_id, status, intake{instructions, dataset_ids,
  integration_ids, budget}, iterations[], cost_so_far_usd, created_at, updated_at`.
- `Iteration`: `n, plan, candidate{kind, artifact_uri, params}, metrics{agreement,
  user_label_accuracy, per_class, latency_ms, size_mb, cost_per_request, projected_net_savings},
  report_md, user_response{action, feedback}`.
- `Dataset`: `id, owner, s3_uri, columns_map, use(train|eval|both), rows, pii_status`.
- `Integration`: `id, type(http|aws|module), target, role, secret_arn, status(pending|
  approved|rejected), approved_by`.
- New tables (Alembic migrations): `agent_runs`, `datasets`, `integrations`.

### 11.5 API
- `GET /api/patterns/{id}/intake` — summary + suggested plan.
- `POST /api/agent/runs` — create a run from intake answers (status `awaiting_intake` →
  `agent_running` on Start).
- `POST /api/datasets/upload-url`, `POST /api/datasets` (register + validate).
- `POST /api/integrations`, `POST /api/integrations/{id}/approve` (admin).
- `GET /api/agent/runs/{id}`, `GET /api/agent/runs/{id}/events` (SSE live progress).
- `POST /api/agent/runs/{id}/respond` — `{action: accept|improve|add_input|stop, feedback}`.
- `POST /api/agent/runs/{id}/budget` — raise the cap.
- `GET /api/notifications` — recognized patterns and runs waiting for the user.

### 11.6 UI (in `web/`)
- **Notifications** (bell in TopNav): "New pattern recognized — ~N tokens/day could be
  saved", and "Agent is waiting for your feedback".
- **Intake screen** (`/patterns/[id]/intake`): the summary, the three questions (with
  dataset upload and column mapping, and the integration form), the budget, and a **Start**
  button.
- **Agent run screen** (`/agents/[runId]`): live timeline of steps (SSE); each iteration's
  report with metrics, a confusion/error table and a model card; the response bar
  (Accept / Keep improving + feedback / Add data or integration / Stop); and cost so far
  against the budget.
- **Integrations admin** (`/tools/integrations`): pending integrations to approve or reject.

### 11.7 Infrastructure
Terraform: ECS task definition `cascade-agent`; a SageMaker execution role; S3 prefixes for
datasets, models and modules; Secrets Manager access scoped to `cascade/integrations/*`; ECR
repos for `ml-runtime`/`dl-runtime`; a security-group/egress pattern for artifacts with
integrations. The new tables come from migrations, not Terraform. The agent's IAM role can
start SageMaker jobs, read and write its S3 prefixes, and reach the database, and nothing
else.

### Phase 5 acceptance
- [ ] **P5-01** Discovery recognizes a pattern → the user is notified → intake shows the summary and
      plan and asks the three questions → nothing runs until **Start**.
- [ ] **P5-02** An uploaded labelled CSV is validated, PII-scanned and used, with its labels
      taking precedence in evaluation.
- [ ] **P5-03** A fuzzy classification pattern: the agent's rule attempt fails, it trains `ml`, and
      reports. The user says "Keep improving: treat 'refund' as its own class"; the next
      iteration reflects it; the user accepts; verification passes; the artifact deploys as a
      container Lambda and serves traffic with confidence-based fallback.
- [ ] **P5-04** A `dl` run trains through the training-job interface (the local trainer with a tiny
      model during the build; SageMaker in AWS) and reaches the same flow.
- [ ] **P5-05** A registered HTTP integration cannot be called by the agent until an admin approves
      it; the resulting artifact shows "not fully deterministic".
- [ ] **P5-06** The run pauses at its budget cap; all agent tokens, labelling and training costs
      appear as ledger overhead, and payback includes them.
- [ ] **P5-07** Killing the agent task mid-run and starting a new one resumes from the saved state.

---

## 12. Phase 6 — Productionize

- **Auth:** Cognito; user identity from the JWT replaces `X-User-Id`; protect all `/api/*`
  except `/api/health`; admin role required for approve/config.
- **HTTPS:** ACM certificate, ALB `:443`, redirect `:80`.
- **Web hosting:** static export of `web/` to S3 + CloudFront; CORS on the API for the
  CloudFront origin.
- **Retention:** a scheduled job deletes logs, raw prompts, conversation turns and expired
  cache entries past their TTL; S3 lifecycle rules on datasets and old document versions.
- **Access groups:** knowledge principals come from the Cognito groups claim instead of the
  v1 `user_groups` table.
- **CI/CD:** GitHub Actions — API image → ECR → ECS; runtime images → ECR; web → S3 +
  CloudFront invalidation; `terraform plan` on PRs.
- **Auth modes:** `CASCADE_AUTH=cognito` in production; `CASCADE_AUTH=local` (persona
  switcher) for development and the demo.
- **DEPLOY.md:** the manual deployment runbook:
  - credentials and Bedrock model access;
  - filling in `model_prices`;
  - `terraform apply`, image pushes and the web upload;
  - smoke checks;
  - `make test-aws` and `make demo-measure`.

### Phase 6 acceptance
- [ ] **P6-01** With `CASCADE_AUTH=cognito`, requests without a valid JWT get 401 (except
      `/api/health`). A token from the test issuer maps its group claims to the right
      principals, and non-admins cannot approve patterns or change configuration (tested
      with a local JWKS fixture).
- [ ] **P6-02** With `CASCADE_AUTH=local`, the persona switcher works for development and the demo.
- [ ] **P6-03** The `next build` static export works against a configurable API origin, and the
      production API config allows CORS only from that origin.
- [ ] **P6-04** The retention job deletes expired logs, prompts, conversation turns and cache entries,
      and nothing else (tested with frozen time).
- [ ] **P6-05** `terraform validate` passes with the HTTPS listener, Cognito, CloudFront and S3 site
      resources. The GitHub Actions workflows pass `actionlint` (when installed) and run
      `make verify`.
- [ ] **P6-06** DEPLOY.md covers every manual AWS step end to end.

## 13. Phase 7 — Scale

- Move the semantic cache and pattern kNN from in-memory hnswlib into pgvector in the same
  database, so all API tasks share one index. Add ElastiCache for the hot exact cache.
- Artifact Lambdas in private subnets with **no NAT** (no outbound network).
- Ledger rollups (hourly/daily aggregate tables or materialized views) and an Aurora read
  replica for dashboard queries, so the dashboard stays fast at volume.
- Retrieval at scale: tune HNSW parameters, partition `chunks` by collection when large, and
  measure recall against a labelled query set.
- Load test: stable p95 latency at target QPS.

### Phase 7 acceptance
- [ ] **P7-01** The semantic cache and pattern kNN run on pgvector and give the same matches as the
      hnswlib implementation on the evaluation set. The in-memory index remains the
      SQLite/quick-start path.
- [ ] **P7-02** Ledger rollups give exactly the same totals as the raw ledger (reconciliation test),
      and the dashboard reads from them.
- [ ] **P7-03** Terraform places artifact Lambdas in private subnets with no route to a NAT gateway.
- [ ] **P7-04** `make perf` on the build machine:
      - light layer p95 under 25 ms (under 60 ms knowledge-scoped) over 1,000 prompts;
      - a 60-second run at 50 requests/s with no errors;
      - artifact-route p95 under 150 ms.
- [ ] **P7-05** Retrieval recall@5 on the labelled set is no lower than the Phase 3 baseline.

---

## 14. Testing strategy

Testing is part of every step, not a final phase. **`make verify` is the single gate.** It
runs every suite below except the opt-in AWS tests, and writes three reports:
- `reports/summary.md`: one line per suite with pass/fail, counts and duration;
- `reports/coverage/`;
- `reports/acceptance.md` (§14.3).

A step is not done until `make verify` exits 0.

### 14.1 Test suites
| Suite | What it covers | Tooling | Make target |
|---|---|---|---|
| Static | lint and formatting; types (mypy strict on `app/light`, `app/rag`, `app/ledger.py`, `app/engine.py`, `app/verifier.py`); ESLint; `tsc --noEmit`; `terraform fmt -check` and `validate`; `actionlint` if installed | ruff, mypy, ESLint, tsc, terraform | `make lint` |
| Backend unit | every module; fast, no network, no Docker | pytest, hypothesis (normalizer, slots, fingerprints), moto, time-machine | `make test-unit` |
| Backend integration | Postgres + pgvector behaviour: migrations, SQL access filters, hybrid search, ledger aggregates, version swaps | pytest against the docker-compose Postgres | `make test-int` |
| API contract | the web client's types match the API | FastAPI OpenAPI → `openapi-typescript` → `web/lib/api-types.ts`; fails if regenerating changes the file | `make contract` |
| Web unit | components and hooks | Vitest + React Testing Library | `make test-web` |
| End to end | the full local stack in a browser, including every demo scenario (§17) | Playwright | `make test-e2e` |
| Evaluations | quality numbers with thresholds (§14.4) | pytest + labelled sets in `demo/data/eval/` | `make eval` |
| Performance | light-layer latency and a short load test (Phase 7 targets) | pytest-benchmark, Locust (headless) | `make perf` |
| Security | dependency audit, static security lint, secret scan; tests that generated code never runs in the API process and that access filters can't be bypassed | pip-audit, bandit, `npm audit --omit=dev --audit-level=high`, gitleaks if installed | `make security` |
| AWS (opt-in) | real Bedrock, Lambda, S3 and SageMaker calls against a sandbox account | pytest `-m aws` | `make test-aws`; **never run during the build** |

### 14.2 Rules for tests
- **Deterministic:** seeded random generators; frozen time for TTL, retention and drift
  tests; the Demo LLM with no simulated latency (`CASCADE_DEMO_LATENCY_MS=0`).
- **No real AWS outside `make test-aws` and `make demo-measure`.** Both are run by hand
  after deployment, never during the build.
  - Every other Makefile target exports `AWS_ACCESS_KEY_ID=testing`,
    `AWS_SECRET_ACCESS_KEY=testing`, `AWS_DEFAULT_REGION=us-east-1`,
    `AWS_CONFIG_FILE=/dev/null` and `AWS_SHARED_CREDENTIALS_FILE=/dev/null`. A stray boto3
    call therefore can't reach a real account, even on a machine that has credentials.
  - Unit tests block network access (pytest-socket). Integration and E2E suites may reach
    localhost only.
- **Isolated:** each integration test gets a fresh schema or a rolled-back transaction. E2E
  suites start from the `make demo-reset` state.
- **Honest:**
  - A failing test is fixed by fixing the code. Tests are never deleted, skipped or weakened
    to make them pass.
  - `xfail` is allowed only for behaviour that needs real AWS, and must state the reason and
    the acceptance ID.
- **Flake policy:** E2E tests get one retry. A test that fails twice is a real failure.
- **Coverage:** at least 85 % line coverage on `app/light`, `app/rag`, `app/ledger.py`,
  `app/engine.py` and `app/verifier.py`, and at least 75 % for the backend overall.

### 14.3 Acceptance matrix
Every acceptance item in §7–§13 has an ID (`P1-01`, `P3-04`, …).
- **Coverage:** each ID is covered by at least one automated test that names it:
  `@pytest.mark.acceptance("P3-04")` in Python, or `@P3-04` in a Playwright test title.
- **Report:** `scripts/acceptance_matrix.py` reads the IDs from BUILD.md and the test
  results. It writes `reports/acceptance.md` with one row per ID: the description, the tests
  that cover it, and a status of `pass`, `fail`, `missing` or `aws-only`.
- **`aws-only`:** allowed only where the behaviour needs a real AWS service. Cascade's own
  logic for that item still needs a moto/stub test, and the item is listed as a manual check
  in DEPLOY.md.
- **Gate:** `make verify` fails if any item is `fail` or `missing`.

### 14.4 Quality thresholds (`make eval`)
- **Router:** on the labelled "must not match" set, the false-hit rate is at most 1 %, and
  exactly 0 for slot mismatches, time-sensitive prompts and follow-ups (these are
  rule-guarded). The match rate is at least 95 % on the "must match" set.
- **Retrieval:** recall@5 of at least 0.9 on the labelled question → document set.
- **Verification:** the agreement scores of the seeded artifacts reproduce within ±1
  percentage point between runs.
- **Ledger:** reconciliation is exact. The ledger and overhead rows sum to every summary
  endpoint value, per user, pattern, collection and time bucket.
- **Knowledge answers:** every Demo LLM answer that used context cites at least one source,
  and no answer ever cites a chunk the user cannot read.

## 15. Coding conventions

- Python 3.12, type hints, Pydantic v2; TypeScript strict in `web/`.
- Keep `Store`, `HeavyModel`, `ArtifactRuntime`, `Ledger` interfaces stable; select
  backends via `CASCADE_*` env vars only.
- All config in `app/config.py`; never hard-code region, model, thresholds or prices.
- Secrets never in the repo; use env vars or SSM Parameter Store.
- Update `ARCHITECTURE.md` and `README.md` when a phase changes design or run steps.
- The Makefile is the only entry point. Every new command gets a target.
- Commits and PRs end with the standard Claude Code attribution lines.

---

## 16. One-go build plan

This section is how Claude Code builds everything, from Step 0 to the finished demo, in a
single unattended run.

### 16.1 Before starting (done by you)
1. **Install** and start: Docker (running), Python 3.12 with `uv`, Node.js 20+, Terraform
   1.6+ (used only for validation), `make` and `git`. Update Claude Code (`claude update`),
   because the run uses `/goal` and auto mode.
2. **Unzip the kit and initialise git:**
   `cd cascade-workspace && git init && git add -A && git commit -m "Scaffold and build spec"`.
3. **No AWS credentials.** Run the build in a shell with no `AWS_*` variables, ideally as a
   user with no `~/.aws/` profile. The build never needs credentials, and the Makefile also
   blanks them (§14.2).
4. **Start Claude Code** in the repo in auto mode: `claude --permission-mode auto`. The
   project's `.claude/settings.json` pre-approves the build commands and blocks pushes,
   `terraform apply` and the AWS CLI.
5. **Paste the kickoff goal** from §16.2.

### 16.2 Kickoff goal
```
/goal Build Cascade exactly as specified in BUILD.md, following CLAUDE.md: Step 0, then Phases 1–7 in order, then Step D (demo assembly). Done means: `make verify` exits 0; reports/acceptance.md shows every acceptance ID as pass or aws-only; `make demo-e2e` passes every demo scenario in §17; PROGRESS.md marks every step done with test evidence; every step is committed. Never delete, skip or weaken a test to make it pass, and never call real AWS. If not done after 300 turns, stop and record what remains in PROGRESS.md.
```
To run it without the interactive UI and stream progress:
`claude --permission-mode auto --permission-prompts none -p "/goal …" --output-format stream-json --verbose`
(paste the full goal text in place of `…`).

### 16.3 Build order and definition of done
| Step | Contents | Done when |
|---|---|---|
| **Step 0 — Foundation** | (see the list below) | `make setup`, `make lint` and a smoke test pass; `make verify` runs (suites with no tests yet report "0 tests", not a failure) |
| **Phase 1** | §7, plus the Demo LLM core (§17.1) | all P1 IDs pass; S1 and S2 pass at API level |
| **Phase 2** | §8 | all P2 IDs pass; S1–S3 pass in the browser |
| **Phase 3** | §9 | all P3 IDs pass; S4 and the document-change half of S5 pass |
| **Phase 4** | §10 | all P4 IDs pass; S8 passes |
| **Phase 5** | §11, plus the scripted agent LLM (§17.1) | all P5 IDs pass; S6, S7, S9 and the artifact half of S5 pass |
| **Phase 6** | §12 | all P6 IDs pass (`aws-only` where justified); DEPLOY.md written; the S10 dry run passes |
| **Phase 7** | §13 | all P7 IDs pass; `make perf` meets its targets |
| **Step D — Demo assembly** | §17.3: final seed state, the `/demo` page, `demo/SCRIPT.md`, `make demo-record` backup videos; a whole-build review by the `acceptance-reviewer` subagent, with every correctness gap it reports fixed | everything in the kickoff goal holds |

"All P*n* IDs pass" means each ID's status in `reports/acceptance.md` is `pass`, or
`aws-only` where §14.3 allows it.

**Step 0 contents:**
- **Repo layout:** `backend/` with `pyproject.toml` and uv, `web/`, `demo/`, `scripts/`,
  `reports/`, `docs/decisions.md`.
- **Makefile** with every target in §14 and §17.
- **docker-compose** with `postgres` (pgvector), `api`, `agent-worker`, `ingest-worker`,
  `web` and `crm-mock`.
- **CI:** a GitHub Actions workflow that runs `make verify`.
- **Demo data:** the generator and labelled evaluation sets (§17.1). They come first because
  every later phase tests against them.
- **Scripts and skeletons:** the acceptance-matrix script, `SqlStore` with the Alembic
  baseline, and a Next.js skeleton with the theme from `design/theme/`.

**Before moving on, every step also needs:**
- `make verify` exits 0;
- PROGRESS.md updated, with the last lines of the `make verify` output as evidence;
- a review by the `acceptance-reviewer` subagent, with its correctness findings fixed;
- a commit (`feat(step-0): …`, `feat(phase-3): …`).

### 16.4 Local stand-ins for AWS
Every AWS dependency has a local stand-in, so the whole system can be built and tested
without an AWS account.

| AWS piece | During the build and the demo | When testing the AWS code path |
|---|---|---|
| Bedrock (LLM) | Demo LLM (§17.1) | Converse request/response fixtures with botocore Stubber |
| Bedrock embeddings | local fastembed (the default anyway) | Stubber |
| Lambda artifacts | local subprocess runtime | moto (create, update, delete and invoke calls) |
| SageMaker training | local trainer: the same training script run as a subprocess with a tiny model (e.g. `prajjwal1/bert-tiny`) | moto / Stubber for the job API |
| ECS agent and ingest tasks | `agent-worker` and `ingest-worker` containers with the same entrypoints | — |
| Aurora PostgreSQL | `pgvector/pgvector:pg16` in docker-compose | — |
| S3, SQS, Secrets Manager | local filesystem, in-process queue, env vars | moto |
| Cognito | `CASCADE_AUTH=local` with the persona switcher | local JWT issuer fixture (test RSA key + JWKS) |
| Comprehend (PII) | regex detector | Stubber |
| SES | console mailer | — |
| EventBridge schedules | in-process scheduler | — |
| CloudFront + S3 site | `next build` static export served locally | — |
| Terraform | `fmt -check`, `init -backend=false`, `validate` (plus tflint if installed) | — |

### 16.5 If the run stops
Run `claude --continue` in the repo; an active `/goal` is restored when a session resumes.
In a new session, say: "Read PROGRESS.md and continue the build from the current step,
following BUILD.md §16." A run paused by a usage limit continues once the limit resets
(interactive session), or with `claude --continue`.

### 16.6 After the build: deploying to AWS
Deployment is a separate, deliberate step that you run with your own credentials; it is not
part of the one-go build. Follow DEPLOY.md (written in Phase 6):
1. Bedrock model access.
2. Prices in `model_prices`.
3. `terraform apply`, image pushes and the web upload.
4. `make test-aws` against a sandbox account.
5. `make demo-measure` for real numbers.

---

## 17. Demo use cases (20 October)

The demo runs entirely on the local stack: no AWS, and no network needed on stage. Every
scenario is also a Playwright test (`web/e2e/demo/sN-*.spec.ts`, tagged `@demo`), so if
`make demo-e2e` passes the night before, the demo works.

### 17.1 Demo world and data (all fictional)

**Company:** *Northpeak Outfitters*, a fictional outdoor-gear retailer. All people,
documents, policies and numbers are invented. Every screen that shows seeded figures carries
a "Demo data — simulated traffic" label.

**Personas** (switch in the top bar; `CASCADE_AUTH=local`), plus 20 background users who
only generate traffic:
| Persona | Role | Groups |
|---|---|---|
| Ava | Support agent | support |
| Ben | Support lead | support, managers |
| Chen | HR partner | hr, managers |
| Dana | New hire in sales | — |
| Eli | Platform admin | admins |

**Knowledge collections:**
| Collection | Access | Documents (format) |
|---|---|---|
| Employee Handbook | everyone | Handbook v1 (DOCX): a vacation-days-by-country table, parental leave, expenses, remote work. Handbook v2 (DOCX, uploaded live) changes Germany from 30 to 32 days and adds Portugal. |
| Products & Returns | everyone | Product FAQ (Markdown), Returns Policy (HTML) |
| Support Playbook | support | Escalation guide (PDF) |
| Compensation Guide | managers | Pay bands and bonus policy (PDF) |

**Traffic patterns** (`demo/generate.py`, seeded, with ground-truth labels stored for every
prompt):
| Pattern | Task type | Artifact at start | Example prompt |
|---|---|---|---|
| Order details extraction | extraction | `rule`, approved | "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'" |
| Unit conversion | transform | `rule`, approved | "Convert 12 km to miles" |
| Urgency scoring | scoring (1–5) | `dl`, approved | "Rate the urgency 1–5: 'My trek starts tomorrow and my boots haven't arrived'" |
| Vacation days by country | lookup over the handbook | `lookup`, approved (built from Handbook v1) | "How many vacation days do employees in France get?" |
| Ticket triage | classification: billing, shipping, returns, refund, technical, account | none: recognized, awaiting intake | "Classify this support ticket: 'The tent pole snapped on the first night'" |
| Priority routing | routing: queue from category and customer tier | none (used in S9) | "Which queue should handle this ticket from jane@example.com: …" |
| Handbook and product questions | free-form knowledge Q&A | none (shared cache) | "How many weeks of parental leave do we offer?" |
| Open-ended work | generation | never compiled | "Draft a friendly reply to a customer whose tent pole broke" |

About 2,400 prompts over 7 simulated days from 25 users. The mix is roughly:
- 30 % triage, 15 % extraction, 10 % urgency, 5 % conversion;
- 20 % knowledge questions;
- 20 % open-ended work.

Tickets are written with enough variety that keyword rules reach only about 60–75 %
agreement, while a trained model can exceed 90 %. A labelled CSV of 300 tickets
(`demo/data/labelled_tickets.csv`) is provided for the live intake. All emails use
`example.com`.

**Demo LLM** (`CASCADE_LLM=demo`; built in Phase 1, with knowledge answers added in Phase 3)
is a deterministic stand-in for Bedrock:
- **Structured prompts** (classification, extraction, scoring, transform, routing, lookup):
  answers from the generator's ground truth.
- **Knowledge questions:** an extractive answer made of the most relevant sentences of the
  provided context, with `[n]` citations. With no context it says "I couldn't find that in
  the documents you can access."
- **Open-ended prompts:** fluent templated text by intent (reply draft, summary,
  description).
- **Token usage:** computed from the actual assembled prompt (system + context + history +
  question) and the answer, plus a fixed reasoning overhead. All ledger arithmetic runs on
  real request sizes.
- **Simulated latency:** 1.8–3.2 s with seeded jitter (`CASCADE_DEMO_LATENCY_MS`), so the
  speed difference is visible on stage. It is 0 in tests.
- **Visibility:** a "Demo LLM" badge appears in the top bar. Switching to Bedrock is one
  setting in Tools.

**Scripted agent LLM** (Phase 5). In demo mode, a deterministic tool-call script drives the
agent, but all the training and evaluation it triggers are real:
1. Iteration 1 tries a `rule`.
2. Iteration 2 trains `ml` (TF-IDF unigrams + logistic regression).
3. After feedback that mentions refunds, iteration 3 retrains with word bigrams, balanced
   class weights and tuned regularisation.

With Bedrock configured, Claude drives the agent instead.

### 17.2 Scenarios
| ID | Scenario | Phase | Deck "What you'll see" |
|---|---|---|---|
| S1 | Live routing: fast path versus the LLM | 1–2 | A, B |
| S2 | Safety rails | 1–2 | C |
| S3 | Savings dashboard | 2 | D |
| S4 | Knowledge answers, shared safely | 3 | new |
| S5 | Documents change, answers stay correct | 3, 5 | new |
| S6 | From recognized pattern to trained model, with the agent | 4–5 | new (centerpiece) |
| S7 | Lookup artifact built from a document | 5 | new |
| S8 | Drift protection | 4 | C |
| S9 | Integration with an existing system (optional) | 5 | new |
| S10 | Real numbers on Bedrock (optional, before the event) | 6 | D |

**S1 — Live routing.** As Ava:
1. Ask the extraction example. It is served by the `artifact` route in tens of
   milliseconds, with a note "saved ~N tokens".
2. Ask "Draft a friendly reply to a customer whose tent pole broke". It goes to the `llm`
   route, takes about 2–3 s, and shows the tokens and cost spent.
3. Repeat step 1's exact prompt. It is served from the `cache`.

Each answer's "why" panel shows which light-layer stage made the decision.

**S2 — Safety rails.**
- "Convert 5 km to miles", then "Convert 50 km to miles": the second prompt is not served
  from the cache because the slots differ, and it returns the correct value.
- "What's the status of today's shipments?" is not cached, because it is time-sensitive.
- After "How many vacation days in France?", the prompt "and for Spain?" is flagged as a
  follow-up. It skips the caches and artifacts and is answered using the conversation
  history.
- A deliberately ambiguous input gets low confidence from an artifact and falls back to the
  LLM. This uses the urgency artifact, or the triage artifact once S6 has built it.

**S3 — Savings dashboard.** The seeded week shows:
- headline tiles: tokens spent, tokens avoided, token reduction, net $ saved after overhead,
  and savings rate;
- baseline versus actual cost over time;
- the route mix;
- a per-user table;
- per-pattern payback, e.g. "Order extraction paid back after N requests" (computed, never
  hard-coded);
- the input mix, including the context share of knowledge questions.

All seeded figures carry the simulated-data label.

**S4 — Knowledge answers, shared safely.**
1. Dana asks "How many weeks of parental leave do we offer?". It goes to the `llm` route,
   with a handbook citation.
2. Ava asks the same question. It is a `cache` hit, shared because the sources match.
3. Dana asks "What's the bonus range for team leads?". She has no access to the
   Compensation Guide, so she gets "couldn't find that in the documents you can access",
   with no citations and no leaked content.
4. Ben, a manager, asks the same question and gets an answer with a citation.

**S5 — Documents change, answers stay correct.**
1. Chen uploads Handbook v2, from the `/demo` page or the Knowledge view.
2. Dana asks again "How many vacation days do employees in Germany get?". The cache misses,
   and the new answer is "32", citing v2.
3. The vacation-days `lookup` artifact, built from v1, is re-verified, demoted, and flagged
   on its pattern card. It stays demoted until it is rebuilt from v2, which is one click on
   the `/demo` page.

**S6 — From recognized pattern to trained model (centerpiece).**
1. The bell shows "New pattern recognized: Ticket triage — projected savings ~N
   tokens/day".
2. Intake:
   - Instructions: "Use these six classes; refund is separate from returns; prefer accuracy
     over latency".
   - Dataset: upload `labelled_tickets.csv` for training and evaluation.
   - Integrations: none.
3. The budget and the payback projection are shown. Click **Start**.
4. Iteration 1 (`rule`) reports low agreement and recommends ML.
5. Iteration 2 (`ml`) reports high agreement. Its error table shows confusion between refund
   and returns.
6. Choose **Keep improving** with the feedback "Refund means the customer wants money back;
   returns means sending the item back". Iteration 3 improves agreement.
7. **Accept**. The verification gate passes, Eli approves, and the artifact is deployed.
8. Back in the Console, a new ticket is classified by the artifact in milliseconds.

The test checks thresholds, not exact numbers: iteration 1 below 80 %, the final iteration
at least 90 %, and the final iteration no worse than iteration 2.

**S7 — Lookup from a document.** Dana asks "How many vacation days do employees in France
get?". The `lookup` artifact answers in milliseconds with the handbook citation, and its
model card lists its `kb_dependencies`. Run S7 before S5.

**S8 — Drift protection.**
1. On the `/demo` page, "Inject faulty extraction artifact" swaps in a version that misreads
   amounts.
2. "Run drift check" runs drift sampling. Agreement drops, the artifact is automatically
   demoted to `shadow`, and its traffic goes back to the LLM.
3. The pattern card shows an alert, and the drift cost appears as overhead.

**S9 — Integration (optional).**
1. The Priority routing intake registers the HTTP integration "CRM tier lookup" (served by
   `crm-mock`).
2. Eli approves the integration.
3. The resulting artifact uses customer tier and is flagged "not fully deterministic".

**S10 — Real numbers (optional, before the event).** `make demo-measure` replays a
200-prompt sample against real Bedrock:
- It prints a cost estimate first, and enforces a hard cap (`--max-usd`, default 5).
- It writes `reports/measured.md` with measured token reduction and latency, for the deck.
- It needs the AWS setup from DEPLOY.md, so the build only implements it and tests a dry
  run.

### 17.3 Demo tooling and starting state
- **Commands:**
  - `make demo-up`: start the stack, migrate and seed.
  - `make demo-reset`: return to the starting state in under 30 seconds.
  - `make demo-e2e`: run every scenario headless.
  - `make demo-record`: record a Playwright video of each scenario into `demo/backup/`, as a
    fallback if anything fails live.
  - `make demo-measure`: run S10.
- **`/demo` page** (only when `CASCADE_DEMO=true`). It shows the scenario cards in
  run-of-show order, plus a Reset button. Each card has:
  - the persona to switch to;
  - talking points;
  - "Run step" buttons for the scripted actions: replay traffic, upload Handbook v2, inject
    a faulty artifact, run a drift check, run discovery, rebuild the lookup from v2.
- **Starting state after seeding:**
  - 7 days of traffic and ledger entries.
  - Approved artifacts: order extraction (`rule`), unit conversion (`rule`), urgency (`dl`,
    trained once locally on a tiny model and cached), and the vacation-days lookup (from
    Handbook v1).
  - Ticket triage recognized and awaiting intake.
  - The four collections, with Handbook v1.
  - The personas and groups, and `crm-mock` running.
- **`demo/SCRIPT.md`:** a run-of-show of about 12 minutes:
  - **Order:** S1 (2 min) → S2 (1.5) → S7 (0.5) → S4 (2) → S5 (1.5) → S6 (4) → S3 (1,
    closing on the dashboard).
  - **Per scenario:** the persona, the clicks, the expected screen, the talking points, and
    a fallback to the recorded video.
  - **Deck link:** S1–S3 map to the deck's "What you'll see" slide (A–D).

---

## Appendix A — Frontend (Next.js): theme & structure

The UI is **Next.js (App Router) + TypeScript + Tailwind** in `web/`, themed to match the
Cascade deck exactly. Copy `design/theme/tailwind.config.ts` to `web/tailwind.config.ts`
and `design/theme/globals.css` to `web/app/globals.css`, unchanged. `frontend/index.html`
is a behaviour and styling reference only.

**Theme tokens:** primary `#0066cc`, primary-focus `#0071e3`, sky `#2997ff` (on dark only),
ink `#1d1d1f`, muted-80 `#333`, muted-48 `#7a7a7a`, hairline `#e0e0e0`, divider `#f0f0f0`,
canvas `#fff`, parchment `#f5f5f7`, pearl `#fafafc`, tile-1 `#272729`, tile-2 `#2a2a2c`,
tile-3 `#252527`, black `#000`. Radii 8 / 11 / 18 / pill. One shadow only
(`0 5px 30px 3px rgba(0,0,0,.22)`) on a single focal element. Inter for text with tight
headline tracking (`-0.02em`), body 17px; JetBrains Mono for eyebrows (uppercase, `.2em`
tracking), metrics, route badges and token counts. Blue pill buttons, `scale(0.96)` on
press. Font weights 300/400/600/700 only.

**Structure:**
```
web/
├── next.config.js            # output: "export"
├── tailwind.config.ts        # provided
├── app/
│   ├── globals.css           # provided
│   ├── layout.tsx            # next/font vars (--font-inter, --font-mono) + <TopNav/>
│   ├── page.tsx              # Console
│   ├── savings/page.tsx      # Token & cost dashboard
│   ├── knowledge/page.tsx    # Collections, uploads, ingestion status (Phase 3)
│   ├── patterns/page.tsx     # Library + <PatternDrawer/>
│   ├── patterns/[id]/intake/page.tsx   # Agent intake (Phase 5)
│   ├── agents/[runId]/page.tsx         # Live agent run + feedback (Phase 5)
│   ├── tools/page.tsx        # Configuration
│   ├── tools/integrations/page.tsx     # Integration approvals (Phase 5)
│   └── demo/page.tsx                   # Demo control page (§17.3; only when CASCADE_DEMO=true)
├── components/
│   ├── TopNav.tsx            # black bar, ◆ Cascade, tabs, user switcher, health dot
│   ├── RouteBadge.tsx        # cache / artifact / llm
│   ├── TokenNote.tsx         # "1,240 tokens · $0.0062" / "saved ~1,180 tokens" / context vs question split
│   ├── CollectionPicker.tsx  # knowledge collections attached to a conversation (Phase 3)
│   ├── CitationChips.tsx     # sources under an answer; open the snippet (Phase 3)
│   ├── UploadDropzone.tsx    # document upload + ingestion progress (Phase 3)
│   ├── StatTile.tsx          # mono figure + label
│   ├── CostChart.tsx         # baseline vs actual over time
│   ├── PatternCard.tsx       # status, kind, score, projected savings
│   ├── PatternDrawer.tsx     # code or model card, verify, approve/edit/reject
│   ├── NotificationBell.tsx  # recognized patterns / runs awaiting feedback
│   ├── IntakeForm.tsx        # instructions, dataset upload + column mapping, integrations, budget
│   ├── RunTimeline.tsx       # SSE live steps
│   ├── IterationReport.tsx   # metrics, error table, model card, response bar
│   ├── Eyebrow.tsx, Button.tsx
├── e2e/                      # Playwright: feature tests, plus demo scenarios in e2e/demo/
└── lib/
    ├── api.ts                # typed client; NEXT_PUBLIC_API_BASE; sends X-User-Id
    └── api-types.ts          # generated from the API's OpenAPI schema (make contract); never hand-edited
```
Local dev: `NEXT_PUBLIC_API_BASE=http://localhost:8080 next dev`. Production: static export
to S3 + CloudFront (Phase 6).

---

*End of specification. Start with §16.*
