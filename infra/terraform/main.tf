###############################################################################
# Cascade — AWS reference infrastructure (v0 skeleton)
#
# Provisions: ECR, DynamoDB (patterns/cache/logs), S3 (handler artifacts),
# an IAM task role (Bedrock + DynamoDB + Lambda invoke), an ECS Fargate service
# behind an ALB, and CloudWatch logs. Networking uses the account's default VPC
# to keep v0 minimal — replace with a dedicated VPC module before production.
#
# This is a starting point. Review IAM scope, enable HTTPS on the ALB (ACM),
# add Cognito on the UI, and put the frontend on S3 + CloudFront.
###############################################################################

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "me" {}
data "aws_region" "current" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name = var.project
  tags = { Project = var.project, ManagedBy = "terraform" }
}

# --------------------------------------------------------------------- ECR
resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
}

# --------------------------------------------------------------------- DynamoDB
resource "aws_dynamodb_table" "patterns" {
  name         = "${local.name}-patterns"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  tags = local.tags
}

resource "aws_dynamodb_table" "cache" {
  name         = "${local.name}-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  tags = local.tags
}

resource "aws_dynamodb_table" "logs" {
  name         = "${local.name}-logs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  tags = local.tags
}

# --------------------------------------------------------------------- S3 (handler artifacts)
resource "aws_s3_bucket" "handlers" {
  bucket        = "${local.name}-handlers-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "handlers" {
  bucket = aws_s3_bucket.handlers.id
  versioning_configuration { status = "Enabled" }
}

# --------------------------------------------------------------------- IAM (task role)
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task" {
  statement {
    sid     = "Bedrock"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}",
      "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_embed_model_id}",
    ]
  }
  statement {
    sid       = "DynamoDB"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.patterns.arn, aws_dynamodb_table.cache.arn, aws_dynamodb_table.logs.arn]
  }
  statement {
    sid       = "HandlerArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.handlers.arn}/*"]
  }
  statement {
    sid       = "InvokeHandlers"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${data.aws_caller_identity.me.account_id}:function:${local.name}-handler-*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${local.name}-task-policy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --------------------------------------------------------------------- Logs
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14
  tags              = local.tags
}

# --------------------------------------------------------------------- Networking (SGs)
resource "aws_security_group" "alb" {
  name   = "${local.name}-alb"
  vpc_id = data.aws_vpc.default.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "svc" {
  name   = "${local.name}-svc"
  vpc_id = data.aws_vpc.default.id
  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

# --------------------------------------------------------------------- ALB
resource "aws_lb" "api" {
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
  tags               = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"
  health_check {
    path    = "/api/health"
    matcher = "200"
  }
  tags = local.tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# --------------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "main" {
  name = local.name
  tags = local.tags
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = var.container_image != "" ? var.container_image : "${aws_ecr_repository.api.repository_url}:latest"
    essential = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [
      { name = "CASCADE_STORE_BACKEND", value = "dynamodb" },
      { name = "CASCADE_HEAVY_BACKEND", value = "bedrock" },
      { name = "CASCADE_EMBED_BACKEND", value = "bedrock" },
      { name = "CASCADE_HANDLER_RUNTIME", value = "lambda" },
      { name = "CASCADE_BEDROCK_REGION", value = var.region },
      { name = "CASCADE_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "CASCADE_BEDROCK_EMBED_MODEL_ID", value = var.bedrock_embed_model_id },
      { name = "CASCADE_DYNAMO_TABLE_PATTERNS", value = aws_dynamodb_table.patterns.name },
      { name = "CASCADE_DYNAMO_TABLE_CACHE", value = aws_dynamodb_table.cache.name },
      { name = "CASCADE_DYNAMO_TABLE_LOGS", value = aws_dynamodb_table.logs.name },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.svc.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.http]
  tags       = local.tags
}
