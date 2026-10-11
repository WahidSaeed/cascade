# Cascade — demo run-of-show (≈ 12 minutes)

Order: **S1** (2 min) → **S2** (1.5) → **S7** (0.5) → **S4** (2) → **S5** (1.5) → **S6** (4) → **S3** (1, close on
the dashboard). S8 and S9 are spares if there is time or a question invites them.

**Before the audience arrives**

1. `make demo-up` (or, if it is already running, `make demo-reset` — under 30 s). Once, well before the event:
   `make demo-e2e-stack` runs every scenario against this exact stack (Postgres, agent-worker, live latency).
2. Open the web app, then the **Demo** tab (`/demo/`). Check the header says *Demo mode · seed phase5-v2*.
3. Live latency: the Demo LLM answers in ~2–3 s, as a real model would (`CASCADE_DEMO_LATENCY_MS`).
4. Keep `demo/backup/` open in a second window: one recorded video per scenario (`make demo-record`). If
   anything misbehaves live, say "here is the same flow recorded this morning" and play the video.

Everything shown is fictional (Northpeak Outfitters); emails use example.com; seeded figures carry the
*simulated* label.

**Deck link:** S1–S3 are the deck's "What you'll see" slide — A and B (S1), C (S2, and S8), D (S3).

---

## S1 — Live routing: fast path versus the LLM (2 min) · persona **Ava** (support agent)

| Click | Expected screen |
|---|---|
| Console → ask: *Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'* | `artifact` badge, tens of ms, JSON with `NP-48213`, note "saved ~N tokens" |
| Ask: *Draft a friendly reply to a customer whose tent pole broke* | `llm` badge, ~2–3 s, tokens and $ spent |
| Ask the extraction prompt again (exact repeat) | `cache` badge, instant |
| Open **why** on each answer | Pattern recognizer / heavy LLM / Exact cache |

Talking points
- Most prompts a support team sends are the same few tasks with different values. Cascade notices them and
  compiles each into a small verified program — no LLM call, no tokens.
- Everything else still goes to the LLM, unchanged. The *why* panel shows which stage decided.
- Every answer carries its token cost or the tokens it avoided. That is the ledger behind the dashboard.

Fallback: `demo/backup/S1-live-routing.webm`.

## S2 — Safety rails (1.5 min) · persona **Ava**, then **Dana**

| Click | Expected screen |
|---|---|
| *Convert 5 km to miles*, then *Convert 50 km to miles* | second is **not** a cache hit (slots differ) and says 31.07 miles |
| *What's the status of today's shipments?* twice | never cached (time-sensitive) |
| Switch to **Dana**: *How many vacation days in France?* then *and for Spain?* | follow-up flagged; caches and artifacts skipped; answered from the conversation |
| Switch to **Ava**: *Rate the urgency 1-5: 'hmm'* | the urgency `dl` artifact has low confidence → falls back to the LLM |

Talking points
- A cache that returns the 5 km answer for 50 km is worse than no cache. Slots must match exactly.
- Time-sensitive and follow-up prompts never take a shortcut.
- An artifact that is unsure says so, and the LLM answers. Wrong-but-fast never ships.

Fallback: `demo/backup/S2-safety-rails.webm`.

## S7 — Lookup from a document (0.5 min) · persona **Dana**

| Click | Expected screen |
|---|---|
| *How many vacation days do employees in France get?* (attach the Handbook collection) | `artifact` in ms, answer **25**, citation *Employee Handbook v1* |
| Patterns → *Vacation days by country* | model card lists its `kb_dependencies` (Handbook v1) |

Talking point: some knowledge questions are really table lookups. Cascade builds a lookup from the
document itself and keeps the citation. Run this **before S5**.

Fallback: `demo/backup/S7-lookup-from-document.webm`.

## S4 — Knowledge answers, shared safely (2 min) · **Dana** → **Ava** → **Dana** → **Ben**

| Click | Expected screen |
|---|---|
| Dana: *How many weeks of parental leave do we offer?* | `llm`, answer 16 weeks, handbook citation |
| Ava: same question | `cache` hit — shared, because the sources match |
| Dana: *What's the bonus range for team leads?* | "couldn't find that in the documents you can access" — no citations, nothing leaked |
| Ben (manager): same question | answered, with a Compensation Guide citation |

Talking points
- Cached knowledge answers are keyed to the exact sources behind them, so they are shared only between people
  who could read those sources.
- Access control runs in the retrieval query itself; Dana never gets a snippet of the compensation guide.

Fallback: `demo/backup/S4-knowledge-shared.webm`.

## S5 — Documents change, answers stay correct (1.5 min) · **Chen**, then **Dana**

| Click | Expected screen |
|---|---|
| Demo page → S5 → **Upload Handbook v2** (or Knowledge → upload as Chen) | ingestion completes; Handbook is v2 |
| Dana: *How many vacation days do employees in Germany get?* | cache misses; answer **32**, citing **v2** |
| Patterns | *Vacation days by country* re-verified and **demoted**, with an alert on its card |
| Demo page → **Rebuild lookup from v2** | the lookup is rebuilt, verified and approved again (dependencies: Handbook v2) |

Talking point: when a document changes, everything built on it is re-checked automatically. Old answers
stop serving; nobody has to remember.

Fallback: `demo/backup/S5-documents-change.webm`.

## S6 — From recognized pattern to trained model (4 min, centerpiece) · **Eli** (admin)

| Click | Expected screen |
|---|---|
| Bell → *New pattern recognized: Ticket triage — projected savings ~N tokens/day* | intake opens with a suggested plan |
| Instructions: *Use these six classes; refund is separate from returns; prefer accuracy over latency* | |
| Dataset: upload `demo/data/labelled_tickets.csv`, use *both* → **Upload** | report: 300 rows, PII scan, duplicates |
| Budget + payback | "pays back after ~N requests" → **Start** |
| Iteration 1 (`rule`) | low agreement (< 80 %), recommends ML → **Keep improving** |
| Iteration 2 (`ml`) | high agreement; error table shows refund ↔ returns confusion |
| Feedback: *Refund means the customer wants money back; returns means sending the item back* → **Keep improving** | iteration 3 ≥ iteration 2 (≥ 90 %) |
| **Accept** | verification gate passes |
| Patterns → *Ticket triage* → **Approve** | deployed |
| Console (Ava): *Classify this support ticket: 'The buckle on the Summit boots cracked on day one'* | `artifact` in ms: **technical** |

Talking points
- Cascade asked before it spent anything: instructions, data, integrations, budget and payback.
- The agent tries the cheapest thing first and tells you why it moves on.
- You steer it in plain language. It cannot ship anything on its own: the verification gate and a human
  approval decide. Every agent token is booked as overhead against the pattern's savings.

Fallback: `demo/backup/S6-agent-builds-model.webm`.

## S3 — Savings dashboard (1 min, close here) · any persona

| Click | Expected screen |
|---|---|
| **Savings** | tiles: tokens spent, tokens avoided, token reduction, net $ saved after overhead, savings rate |
| Scroll | baseline vs actual cost, route mix, per-user table, per-pattern payback ("Order extraction paid back after N requests"), input mix |

Talking points
- Savings are **net**: every compile, verification, agent run, drift check and embedding is subtracted.
- Payback per pattern is computed from the ledger, not asserted.
- The seeded week is labelled simulated; `make demo-measure` (S10) produces the same report against real
  Bedrock.

Fallback: `demo/backup/S3-savings-dashboard.webm`.

---

## Spares

**S8 — Drift protection** · Eli · Demo page → S8 → **Inject faulty extraction artifact** → **Run drift check**.
Agreement drops, the artifact is demoted to `shadow` automatically, its traffic returns to the LLM, the
pattern card shows an alert, and the drift check appears as overhead. Talking point: approved is not forever;
live traffic is sampled against the LLM. Fallback: `demo/backup/S8-drift-protection.webm`.

**S9 — Integration** · Eli · Priority routing intake registers *CRM tier lookup* (served by `crm-mock`) →
Tools → Integrations → **Approve**. The resulting artifact uses the customer tier and is flagged *not fully
deterministic*. Fallback: `demo/backup/S9-integration.webm`.

**S10 — Real numbers** (before the event, not live): `make demo-measure MAX_USD=5` → `reports/measured.md`.

## Reset between rehearsals

Demo page → **Reset** (or `make demo-reset`): restores the seeded snapshot in under 30 s, including Tools
edits.
