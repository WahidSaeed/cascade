#!/usr/bin/env bash
# Build and ship Cascade to the infrastructure `terraform apply` created (DEPLOY.md §4–5). Run from the repository
# root with credentials for the account:  AWS_REGION=eu-central-1 scripts/aws_deploy.sh [images|web|all]
# Images are built for linux/amd64 (Fargate and Lambda default to x86_64), also on Apple-silicon machines.
set -euo pipefail
WHAT="${1:-all}"
REGION="${AWS_REGION:-$(aws configure get region)}"
out() { terraform -chdir=infra/terraform output -raw "$1"; }
export BUILDX_NO_DEFAULT_ATTESTATIONS=1   # single-arch manifests: Lambda rejects image indexes
build() { docker build --platform linux/amd64 "$@"; }

if [[ "$WHAT" == images || "$WHAT" == all ]]; then
  API=$(out ecr_repository_url); REG=${API%%/*}
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG"
  ML=$(terraform -chdir=infra/terraform output -json runtime_repository_urls | python3 -c 'import json,sys; print(json.load(sys.stdin)["ml-runtime"])')
  DL=$(terraform -chdir=infra/terraform output -json runtime_repository_urls | python3 -c 'import json,sys; print(json.load(sys.stdin)["dl-runtime"])')
  TRAIN=$(out training_repository_url)
  build -f backend/Dockerfile -t "$API:latest" . && docker push "$API:latest"
  build -f infra/runtime-images/ml-runtime.Dockerfile -t "$ML:latest" . && docker push "$ML:latest" || echo "ml-runtime: already pushed (immutable tag)"
  build -f infra/runtime-images/dl-runtime.Dockerfile -t "$DL:latest" . && docker push "$DL:latest" || echo "dl-runtime: already pushed (immutable tag)"
  build -f backend/Dockerfile --build-arg WITH_DL=1 -t cascade-api:dl-amd64 .
  build -f infra/runtime-images/training.Dockerfile --build-arg BASE=cascade-api:dl-amd64 -t "$TRAIN:latest" . && docker push "$TRAIN:latest"
  for svc in api agent; do
    aws ecs update-service --region "$REGION" --cluster cascade --service "cascade-$svc" --force-new-deployment >/dev/null
  done
fi

if [[ "$WHAT" == web || "$WHAT" == all ]]; then
  (cd web && NEXT_PUBLIC_API_BASE=$(out api_url) NEXT_PUBLIC_AUTH=cognito \
    NEXT_PUBLIC_COGNITO_DOMAIN=$(out cognito_domain) NEXT_PUBLIC_COGNITO_CLIENT_ID=$(out cognito_client_id) npx next build)
  aws s3 sync web/out/ "s3://$(out web_bucket)/" --delete --region "$REGION"
  aws cloudfront create-invalidation --distribution-id "$(out web_distribution_id)" --paths "/*" >/dev/null
  echo "web: $(out web_url)"
fi
