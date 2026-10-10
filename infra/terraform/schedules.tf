# --------------------------------------------------------------------- discovery + drift schedules (§10)
resource "aws_ecs_task_definition" "jobs" {
  family                   = "${local.name}-jobs"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name        = "jobs"
    image       = local.image
    essential   = true
    command     = ["python", "-m", "app.jobs", "discover"]
    environment = local.backend_env
    secrets     = local.backend_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "jobs"
      }
    }
  }])
  tags = local.tags
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${local.name}-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "events_run_task" {
  statement {
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.jobs.arn_without_revision, "${aws_ecs_task_definition.jobs.arn_without_revision}:*"]
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task.arn, aws_iam_role.execution.arn]
  }
}

resource "aws_iam_role_policy" "events_run_task" {
  name   = "${local.name}-events-run-task"
  role   = aws_iam_role.events.id
  policy = data.aws_iam_policy_document.events_run_task.json
}

locals {
  schedules = {
    discover = { expression = "cron(0 3 * * ? *)", command = ["python", "-m", "app.jobs", "discover"] }
    drift    = { expression = "rate(1 hour)", command = ["python", "-m", "app.jobs", "drift"] }
  }
}

resource "aws_cloudwatch_event_rule" "jobs" {
  for_each            = local.schedules
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.expression
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "jobs" {
  for_each = local.schedules
  rule     = aws_cloudwatch_event_rule.jobs[each.key].name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.events.arn
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.jobs.arn
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.svc.id]
      assign_public_ip = true
    }
  }
  input = jsonencode({ containerOverrides = [{ name = "jobs", command = each.value.command }] })
}
