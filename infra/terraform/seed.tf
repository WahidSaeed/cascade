# One-off task that loads the demo starting state into this deployment (`python -m app.seed --build`): it ingests
# documents and deploys artifact Lambdas like the API, and trains the dl artifact like the agent, so its role carries
# both policies. It runs the training image (the backend with torch), so the dl artifact can also train inside the
# task (CASCADE_DL_TRAINER=local) on accounts without SageMaker training quota. Nothing runs it on a schedule; start
# it by hand with `aws ecs run-task` (DEPLOY.md).
resource "aws_iam_role" "seed" {
  name               = "${local.name}-seed"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "seed_api" {
  name   = "${local.name}-seed-api"
  role   = aws_iam_role.seed.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_iam_role_policy" "seed_agent" {
  name   = "${local.name}-seed-agent"
  role   = aws_iam_role.seed.id
  policy = data.aws_iam_policy_document.agent.json
}

resource "aws_ecs_task_definition" "seed" {
  family                   = "${local.name}-seed"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.seed.arn
  container_definitions = jsonencode([merge(jsondecode(aws_ecs_task_definition.agent.container_definitions)[0], {
    name    = "seed"
    image   = "${aws_ecr_repository.training.repository_url}:latest"
    command = ["python", "-m", "app.seed", "--build"]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "seed"
      }
    }
  })])
  tags = local.tags
}
