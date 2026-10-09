#!/usr/bin/env bash
# Build the API image and push it to the ECR repo created by Terraform.
# Usage:  REGION=us-east-1 scripts/push.sh
set -euo pipefail

REGION="${REGION:-us-east-1}"
PROJECT="${PROJECT:-cascade}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-api"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

# build from repo root so both backend/ and frontend/ are in the context
docker build -f backend/Dockerfile -t "${PROJECT}-api:latest" .
docker tag "${PROJECT}-api:latest" "${REPO}:latest"
docker push "${REPO}:latest"

echo "pushed ${REPO}:latest"
echo "now run: cd infra/terraform && terraform apply -var=\"container_image=${REPO}:latest\""
