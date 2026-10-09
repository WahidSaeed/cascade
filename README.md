# Cascade

> **Start here.** This repo is a build kit. `BUILD.md` is the full specification, and §16 of
> it explains how Claude Code builds everything, with tests and the demo, in one unattended
> run:
> ```bash
> git init && git add -A && git commit -m "Scaffold and build spec"
> claude --permission-mode auto      # then paste the /goal from BUILD.md §16.2
> ```
> The rest of this README describes the original scaffold. Claude Code updates it as the
> build progresses.

A workspace for serving repetitive LLM traffic from a cheap, deterministic **fast path**,
reserving a heavy reasoning model for genuinely novel work.

It has four parts, in one app:

- **Console** — a chat interface; every answer is tagged with the path that served it
  (cache / fast path / heavy model), plus latency and confidence.
- **Pattern library** — every deterministic handler the system generates, shown for
  review. See the code, **verify** it against the heavy model, **edit** it, then
  **approve it into routing** (or reject it). Editing code resets it to draft for re-verification.
- **Tools** — configure the stack: heavy-model backend, embeddings, routing thresholds,
  the fast-path sandbox, and the approval gate.
- **Metrics** — route breakdown, cheap-path share, and latency per route.

See `ARCHITECTURE.md` for the full design and the AWS service mapping.

## Run locally (offline, no AWS, no credentials)

The heavy model runs as a deterministic **mock** and embeddings are computed locally, so
the whole flow works with zero setup.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed            # seeds a demo pattern + sample traffic
uvicorn app.main:app --reload --port 8080
```

Open <http://localhost:8080>. Try it:

1. **Patterns → Review** the seeded "Sentiment classifier" → **Run verification** → **Approve into routing**.
2. **Console:** ask `What is the sentiment of: I love this` → served by the **fast path**.
   Ask `Write a poem` → served by the **heavy model**.
3. **Metrics:** watch the cheap-path share rise.

Or with Docker: `docker compose up --build`.

> Note: locally the heavy model is an instant mock, so the *latency* win only appears once
> you point it at Bedrock (real reasoning calls take seconds; the fast path stays in ms).

## Deploy to AWS

Everything becomes real by switching backends (DynamoDB, Bedrock, Lambda). The reference
infrastructure is in `infra/terraform/`.

**Prerequisites:** an AWS account, the AWS CLI configured (`aws configure`), Docker,
Terraform ≥ 1.6, and **Bedrock model access enabled** for your chosen models in the
console (Bedrock → Model access).

```bash
# 1. Create the infrastructure (ECR, DynamoDB, S3, IAM, ECS/Fargate, ALB)
cd infra/terraform
terraform init
terraform apply                      # note the ecr_repository_url output

# 2. Build & push the API image
cd ../..
REGION=us-east-1 scripts/push.sh

# 3. Point the service at the pushed image
cd infra/terraform
terraform apply -var="container_image=<ECR_URL>:latest"

# 4. Open the app
terraform output alb_url
```

### What's real vs. still stubbed

| Real now | Stubbed / next |
|---|---|
| Routing engine (cache → pattern → heavy), pattern lifecycle, verify/approve gate, full UI, local execution | Deploying an **approved handler as a Lambda** (the approve step sets status; wiring the deploy call is marked `TODO` in `app/main.py`) |
| DynamoDB + Bedrock + ECS/Fargate + ALB via Terraform | HTTPS on the ALB (add ACM cert), Cognito auth on the UI, frontend on S3+CloudFront |
| Semantic cache + pattern match (cosine) | Vector store (OpenSearch/pgvector) for scale; ElastiCache for the cache |

### Security boundary (important)

Generated handler code is **never** executed in the API process. Locally, `subprocess`
mode runs it in a separate process **for development only — not a security boundary**. In
AWS, set `CASCADE_HANDLER_RUNTIME=lambda` so each approved handler runs as its own isolated
Lambda with a least-privilege role and no network. Do not expose `subprocess` mode to
untrusted input.

## Swap point: self-hosted models

The default heavy model is **Amazon Bedrock** (managed, no GPUs to run). To use open
weights instead, run an OpenAI-compatible server (e.g. SGLang/vLLM) on EC2/EKS and add a
`heavy.py` backend that points at it — the rest of the system is unchanged.
