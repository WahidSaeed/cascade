# Cascade — Architecture

Cascade serves repetitive LLM traffic from a cheap, deterministic **fast path** and
reserves a heavy reasoning model for genuinely novel work. This document describes the
system and how it maps onto AWS.

## Core idea

```
                          ┌─────────────────────────────┐
   prompt  ──────────────▶│   Router  (System 1, light) │
                          └──────────────┬──────────────┘
             ┌───────────────────────────┼───────────────────────────┐
             ▼                           ▼                           ▼
       A. Cache hit               B. Known pattern              C. Novel
    (semantic cache)          (deterministic handler)        (heavy model)
             │                           │                           │
             ▼                           ▼                           ▼
       return instant           run handler = Lambda          Bedrock / LLM
             │                           │                           │
             └───────────────┬───────────┴───────────────┬───────────┘
                             ▼                           ▼
                        return answer   ───────▶   log every result
                                                        │
                                          ┌─────────────▼─────────────┐
                                          │  Pattern discovery (async) │
                                          │  cluster → compile → verify│
                                          └─────────────┬─────────────┘
                                                        ▼
                                            Pattern library (review):
                                        approve → edit → promote to routing
```

The **pattern lifecycle** is the heart of the product:

`fingerprint → compile → verify → (human review) → approve → route → monitor`

A handler is only eligible to serve traffic after (1) its verification score clears a
threshold against the heavy model on held-out samples, **and** (2) a human approves it in
the Pattern Library. Approved handlers are deployed as isolated AWS Lambda functions and
invoked by the router on a match.

## Components → AWS services

| Component | Local (dev) | AWS (prod) |
|---|---|---|
| Frontend (console, config, pattern library, metrics) | served by FastAPI at `/` | **S3 + CloudFront** static site |
| Backend API (FastAPI) | `uvicorn` on localhost | container on **ECS Fargate** behind an **ALB** |
| Heavy model (System 2) | mock client, or any API key | **Amazon Bedrock** `InvokeModel` *(swap: self-hosted + SGLang on EC2/EKS)* |
| Embeddings / router (System 1) | deterministic local embedder | **Bedrock** Titan/Cohere embeddings *(slot: a System One model e.g. Jev)* |
| Semantic cache | SQLite table + cosine | **DynamoDB** (+ ElastiCache/Redis later) |
| Pattern store (metadata, versions, scores, status) | SQLite | **DynamoDB** |
| Handler code artifacts | local `./handlers` | **S3** (versioned) |
| Fast-path execution / sandbox | restricted subprocess (DEV ONLY) | one **AWS Lambda** per approved handler (no network, least-priv role) |
| Codegen + verification jobs | inline / background task | Fargate task or Lambda, gated promotion |
| Observability | stdout + `/metrics` | **CloudWatch** (+ optional Langfuse) |
| Auth | none (dev) | **Cognito** on the UI |
| IaC | — | **Terraform** |
| CI/CD | — | GitHub Actions → **ECR** → Fargate |

## Why these defaults

- **Bedrock over self-hosted GPUs** for v0: no GPU fleet to run, pay-per-token, stays
  inside your AWS account and IAM. Swap to self-hosted open weights + SGLang later if you
  need open models or prefix-cache economics at high volume.
- **Lambda as the sandbox** is the single most important safety decision: generated code
  is **never** executed in the API process. Each approved handler is its own function with
  a locked-down role and no outbound network. Editing a handler publishes a new version;
  approving deploys it; rejecting leaves it inert.
- **DynamoDB on-demand + Fargate small + Bedrock pay-per-use** keeps v0 cheap; there is no
  always-on GPU or database cluster.

## Request path (what the backend actually does on `POST /chat`)

1. Embed the prompt (router).
2. **Cache:** nearest cached answer above `CACHE_THRESHOLD` → return (`route=cache`).
3. **Pattern:** best approved pattern above `PATTERN_THRESHOLD` whose input-guard passes →
   invoke its handler (Lambda in prod) → return (`route=fast_path`). If the guard rejects
   the input, fall through.
4. **Heavy:** call Bedrock → return (`route=heavy`).
5. Log the prompt + route + latency for discovery and metrics.

## Safety boundary (read before production)

The dev server can execute a handler in a restricted subprocess so you can try the flow
offline. **That is not a security boundary.** In AWS, set `HANDLER_RUNTIME=lambda` so
handlers run only as isolated Lambda functions. Never enable in-process execution in a
deployment reachable by untrusted input.
