# --------------------------------------------------------------------- model-building agent (§11.7)
resource "aws_ecr_repository" "runtime" {
  for_each             = toset(["ml-runtime", "dl-runtime"])
  name                 = "${local.name}-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.tags
}

resource "aws_iam_role" "agent" {
  name               = "${local.name}-agent"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

# The agent can start SageMaker jobs, read/write its S3 prefixes, call Bedrock and read integration
# secrets — and nothing else (no Lambda deploys, no approvals: those stay with the API and a human).
data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "Bedrock"
    actions   = ["bedrock:InvokeModel", "bedrock:Converse"]
    resources = ["*"]
  }
  statement {
    sid       = "AgentData"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/datasets/*", "${aws_s3_bucket.artifacts.arn}/models/*", "${aws_s3_bucket.artifacts.arn}/modules/*"]
  }
  statement {
    # a training job's uploaded inputs (user-edited code, training and evaluation rows) are deleted when it ends
    sid       = "TrainingInputCleanup"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/datasets/cascade-dl-*"]
  }
  statement {
    sid       = "Training"
    actions   = ["sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob"]
    resources = ["arn:aws:sagemaker:${var.region}:${local.account_id}:training-job/cascade-dl-*"]
  }
  statement {
    sid       = "PassSageMakerRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker.arn]
  }
  statement {
    sid       = "IntegrationSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:CreateSecret"]
    resources = ["arn:aws:secretsmanager:${var.region}:${local.account_id}:secret:cascade/integrations/*"]
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "${local.name}-agent"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent.json
}

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker" {
  name               = "${local.name}-sagemaker"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "sagemaker" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/datasets/*", "${aws_s3_bucket.artifacts.arn}/models/*"]
  }
  statement {
    actions   = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sagemaker" {
  name   = "${local.name}-sagemaker"
  role   = aws_iam_role.sagemaker.id
  policy = data.aws_iam_policy_document.sagemaker.json
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${local.name}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.agent.arn
  container_definitions = jsonencode([{
    name      = "agent"
    image     = local.image
    essential = true
    command   = ["python", "-m", "app.agent.worker"]
    environment = concat(local.backend_env, [
      { name = "CASCADE_AGENT_EXECUTOR", value = "worker" },
      { name = "CASCADE_AGENT_LLM", value = "bedrock" },
      { name = "CASCADE_DL_TRAINER", value = "sagemaker" },
      { name = "CASCADE_SAGEMAKER_ROLE_ARN", value = aws_iam_role.sagemaker.arn },
      { name = "CASCADE_ML_RUNTIME_IMAGE", value = "${aws_ecr_repository.runtime["ml-runtime"].repository_url}:latest" },
      { name = "CASCADE_DL_RUNTIME_IMAGE", value = "${aws_ecr_repository.runtime["dl-runtime"].repository_url}:latest" },
    ])
    secrets = local.backend_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "agent"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "agent" {
  name            = "${local.name}-agent"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.agent.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.svc.id]
    assign_public_ip = true
  }
  tags = local.tags
}

# Artifacts with integrations: egress only to the declared target (one rule per approved integration
# is added by the deployer's runbook; the default allows nothing).
resource "aws_security_group" "artifact_integration" {
  name        = "${local.name}-artifact-integration"
  description = "Lambda artifacts that call an approved integration: egress to declared targets only"
  vpc_id      = aws_vpc.main.id
  tags        = local.tags
}
