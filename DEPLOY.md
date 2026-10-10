# Deploying Cascade to AWS

Deployment is a deliberate, manual step with your own credentials. It is never part of the build:
`make verify` and every other target blank AWS credentials. Only `make test-aws` and `make demo-measure`
use real AWS, and only when you run them by hand after the steps below.

Region in the examples: `us-east-1`. Replace `<account>` with your account id.

## 0. Prerequisites

- AWS CLI v2, Terraform ≥ 1.6, Docker, Node.js 20, `uv`.
- An AWS account (a sandbox account for the first deployment), and a shell with credentials for it:
  `aws sso login --profile cascade` (or `aws configure`), then `export AWS_PROFILE=cascade AWS_REGION=us-east-1`.
- `aws sts get-caller-identity` shows the intended account.

## 1. Credentials and Bedrock model access

1. In the Bedrock console → *Model access*, enable:
   - **Claude Sonnet 4.5** (the heavy model; invoked through the `us.` cross-region inference profile),
   - **Claude Haiku 4.5** (the light LLM used only by discovery),
   - **Titan Text Embeddings v2** (only if you set `CASCADE_EMBED_BACKEND=bedrock-titan`).
2. Check access: `aws bedrock list-inference-profiles --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].inferenceProfileId"`.
3. If you use different model ids, set `bedrock_model_id` / `light_llm_model_id` in `terraform.tfvars`.

## 2. Prices (`model_prices`)

Prices are configuration, never code. Take them from the current pricing pages
(Bedrock: https://aws.amazon.com/bedrock/pricing/, Lambda: https://aws.amazon.com/lambda/pricing/,
SageMaker training: https://aws.amazon.com/sagemaker/pricing/) and set them as one JSON value:

```bash
export CASCADE_MODEL_PRICES='{
  "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
  "us.anthropic.claude-haiku-4-5-20251001-v1:0":  {"input": 1.0, "output": 5.0,  "cache_read": 0.10, "cache_write": 1.25},
  "amazon.titan-embed-text-v2:0": {"input": 0.02, "output": 0.0},
  "_lambda": {"gb_second": 0.0000166667, "request": 0.0000002},
  "_training": {"per_hour": 0.115}
}'
```
(USD per 1M tokens; Lambda per GB-second and per request; training per instance-hour.) After deployment
you can also edit them in **Tools → Model prices**; edits are persisted in the database.

## 3. Infrastructure: `terraform apply`

```bash
cd infra/terraform
cat > terraform.tfvars <<'EOF'
region          = "us-east-1"
api_domain_name = "api.cascade.example.com"   # required: the API is served over HTTPS only (:80 redirects)
EOF
terraform init
terraform plan -out cascade.plan      # review: VPC, Aurora, ECS, ALB, S3, CloudFront, Cognito, SQS, IAM, schedules
terraform apply cascade.plan
terraform output                      # note ecr_repository_url, web_bucket, web_url, cognito_*, artifact_*
```
- **HTTPS (required)**: create the DNS validation record that `aws_acm_certificate.api` asks for
  (`terraform state show aws_acm_certificate.api` → `domain_validation_options`), wait for the certificate to
  be *Issued*, then point `api_domain_name` at the ALB (a CNAME/alias to `terraform output alb_url`).
- The database schema is created by the API on start (Alembic migrations run at startup, idempotent).
- Aurora credentials live in Secrets Manager (`cascade/db`) and reach the tasks as `CASCADE_DATABASE_URL`.

## 4. Images

```bash
REG=<account>.dkr.ecr.us-east-1.amazonaws.com
aws ecr get-login-password | docker login --username AWS --password-stdin $REG
# API (also used by the agent, ingest and jobs tasks); the light-layer models are baked in
docker build -f backend/Dockerfile -t $REG/cascade-api:latest . && docker push $REG/cascade-api:latest
# artifact runtimes (ml / dl Lambda container images)
docker build -f infra/runtime-images/ml-runtime.Dockerfile -t $REG/cascade-ml-runtime:latest . && docker push $REG/cascade-ml-runtime:latest
docker build -f infra/runtime-images/dl-runtime.Dockerfile -t $REG/cascade-dl-runtime:latest . && docker push $REG/cascade-dl-runtime:latest
# the SageMaker training image runs backend/app/compilers/dl_train_script.py; set CASCADE_DL_TRAINING_IMAGE
docker build -f backend/Dockerfile --build-arg WITH_DL=1 -t $REG/cascade-api:dl . && docker push $REG/cascade-api:dl
aws ecs update-service --cluster cascade --service cascade-api --force-new-deployment
aws ecs update-service --cluster cascade --service cascade-agent --force-new-deployment
```
The `cascade-ingest` service scales from 0 on queue depth; it needs no manual start.

## 5. Web upload (S3 + CloudFront)

```bash
cd web
TF="terraform -chdir=../infra/terraform output -raw"
NEXT_PUBLIC_API_BASE=$($TF api_url) NEXT_PUBLIC_AUTH=cognito \
NEXT_PUBLIC_COGNITO_DOMAIN=$($TF cognito_domain) NEXT_PUBLIC_COGNITO_CLIENT_ID=$($TF cognito_client_id) \
  npx next build                                                           # static export into out/
aws s3 sync out/ s3://$(terraform -chdir=../infra/terraform output -raw web_bucket)/ --delete
aws cloudfront create-invalidation --distribution-id <distribution id> --paths "/*"
```
The API only allows CORS from the CloudFront origin (`CASCADE_CORS_ORIGINS`, set by Terraform).

## 6. Users and groups (Cognito)

```bash
POOL=$(terraform -chdir=infra/terraform output -raw cognito_user_pool_id)
aws cognito-idp admin-create-user --user-pool-id $POOL --username eli@example.com
aws cognito-idp admin-add-user-to-group --user-pool-id $POOL --username eli@example.com --group-name admins
```
Sign-in uses the Cognito hosted UI (`terraform output cognito_domain`) with the authorization-code flow +
PKCE; the callback is `https://<cloudfront domain>/auth/callback/` (already registered by Terraform).
Groups (`admins`, `support`, `managers`, `hr`) become knowledge principals (`group:<name>`); only `admins` may
approve artifacts, approve integrations, run discovery and drift checks, and change configuration. Requests without a valid JWT get 401
(except `/api/health`).

## 7. Integrations (rule 11)

Artifacts with an approved integration run in private subnets with the `cascade-artifact-integration`
security group, which allows no egress by default. For each approved integration add exactly one egress
rule to its target, e.g.:
```bash
aws ec2 authorize-security-group-egress --group-id <sg id> --ip-permissions \
  'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=<target ip>/32,Description="CRM tier lookup"}]'
```
Store integration credentials only through the intake form (they go to Secrets Manager
`cascade/integrations/<id>`).

## 8. Smoke checks

```bash
API=https://api.cascade.example.com
curl -s $API/api/health                                   # {"status":"ok","db":"postgresql","llm":"bedrock","auth":"cognito",...}
curl -s -o /dev/null -w "%{http_code}\n" $API/api/patterns # 401 without a token
TOKEN=<an ID token from the hosted UI or `aws cognito-idp initiate-auth`>
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"prompt":"In one sentence, what is a tent footprint?"}' $API/api/chat | jq '.route, .tokens'
```
Then open the CloudFront URL, sign in, ask a question twice (the second is a cache hit), and check that
**Savings** shows both requests.

## 9. Real-AWS tests: `make test-aws`

Against a sandbox account only:
```bash
make test-aws        # runs pytest -m aws with your credentials and CASCADE_LLM=bedrock
```
These are the manual checks for the `aws-only` acceptance items in `reports/acceptance.md`:
- **P1-10** — non-matching prompts get real Bedrock answers with real usage (`test_real_bedrock_answers_with_usage`).
Cascade's own logic for each aws-only item is also covered by moto/Stubber tests in `make verify`.

## 10. Real numbers: `make demo-measure`

```bash
make demo-measure MAX_USD=5     # replays a 200-prompt sample against real Bedrock
```
It prints a cost estimate first and stops at the hard cap (`--max-usd`, default 5). It writes
`reports/measured.md` (measured token reduction and latency) for the deck.

## Rollback

`aws ecs update-service --cluster cascade --service cascade-api --task-definition <previous revision>`;
artifacts can be rejected in **Patterns** (their Lambdas are torn down immediately).
