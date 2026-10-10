resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14
  tags              = local.tags
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  tags = local.tags
}

locals {
  image = var.container_image != "" ? var.container_image : "${aws_ecr_repository.api.repository_url}:latest"
  backend_env = [
    { name = "CASCADE_ENV", value = "prod" },
    { name = "CASCADE_AUTH", value = "cognito" },
    { name = "CASCADE_COGNITO_ISSUER", value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}" },
    { name = "CASCADE_COGNITO_AUDIENCE", value = aws_cognito_user_pool_client.web.id },
    { name = "CASCADE_CORS_ORIGINS", value = "https://${aws_cloudfront_distribution.web.domain_name}" },
    { name = "CASCADE_DEMO", value = "false" },
    { name = "CASCADE_LLM", value = "bedrock" },
    { name = "CASCADE_EMBED_BACKEND", value = "fastembed" },
    { name = "CASCADE_EMBED_THREADS", value = "1" }, # the API task has 1 vCPU (cpu = 1024)
    { name = "CASCADE_ARTIFACT_RUNTIME", value = "lambda" },
    { name = "CASCADE_ARTIFACT_ROLE_ARN", value = aws_iam_role.artifact.arn },
    { name = "CASCADE_ARTIFACT_BUCKET", value = aws_s3_bucket.artifacts.bucket },
    { name = "CASCADE_BEDROCK_REGION", value = var.region },
    { name = "CASCADE_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
    { name = "CASCADE_LIGHT_LLM_MODEL_ID", value = var.light_llm_model_id },
    { name = "CASCADE_PII_BACKEND", value = "comprehend" },
    { name = "CASCADE_INGEST_QUEUE_URL", value = aws_sqs_queue.ingest.url },
    { name = "CASCADE_BLOB_BUCKET", value = aws_s3_bucket.artifacts.bucket },
    { name = "CASCADE_ARTIFACT_SUBNET_IDS", value = join(",", aws_subnet.private[*].id) },
    { name = "CASCADE_ARTIFACT_INTEGRATION_SG", value = aws_security_group.artifact_integration.id },
    { name = "CASCADE_ARTIFACT_SG", value = aws_security_group.artifact.id },
    { name = "CASCADE_EXACT_CACHE_URL", value = "rediss://${aws_elasticache_serverless_cache.exact.endpoint[0].address}:${aws_elasticache_serverless_cache.exact.endpoint[0].port}" },
  ]
  backend_secrets = [
    { name = "CASCADE_DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.db.arn}:url::" },
    { name = "CASCADE_DATABASE_READ_URL", valueFrom = "${aws_secretsmanager_secret.db.arn}:read_url::" },
  ]
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048" # the light-layer embedding model lives in memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment  = local.backend_env
    secrets      = local.backend_secrets
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
    subnets          = aws_subnet.public[*].id
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
