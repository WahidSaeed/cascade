###############################################################################
# Cascade — AWS infrastructure (BUILD.md §7.6, §9.8, §11.7, §12, §13)
#
#   network.tf   dedicated VPC: public subnets (ALB, ECS tasks), private subnets (Aurora, artifacts)
#   data.tf      Aurora PostgreSQL Serverless v2 (+ pgvector), Secrets Manager, S3
#   iam.tf       task/execution roles, the least-privilege artifact execution role
#   ecs.tf       ECR, ECS Fargate (API, 2 GB: the light-layer model is in memory), logs
#   alb.tf       Application Load Balancer
#
# Only `terraform fmt` and `terraform validate` run during the build. Applying is a manual,
# deliberate step (DEPLOY.md).
###############################################################################

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "me" {}
data "aws_region" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = var.project
  account_id = data.aws_caller_identity.me.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  tags       = { Project = var.project, ManagedBy = "terraform" }
}
