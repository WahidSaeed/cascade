# --------------------------------------------------------------------- knowledge ingestion (§9.8)
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600
  tags                      = local.tags
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 300
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
  tags = local.tags
}

data "aws_iam_policy_document" "ingest_queue" {
  statement {
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingest.arn]
  }
  statement {
    sid       = "KnowledgeObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/kb/*"]
  }
}

resource "aws_iam_role_policy" "ingest_queue" {
  name   = "${local.name}-ingest-queue"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.ingest_queue.json
}

resource "aws_ecs_task_definition" "ingest" {
  family                   = "${local.name}-ingest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name        = "ingest"
    image       = local.image
    essential   = true
    command     = ["python", "-m", "app.rag.worker"]
    environment = local.backend_env
    secrets     = local.backend_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ingest"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "ingest" {
  name            = "${local.name}-ingest"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ingest.arn
  desired_count   = 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.svc.id]
    assign_public_ip = true
  }
  lifecycle {
    ignore_changes = [desired_count] # managed by the queue-depth autoscaling below
  }
  tags = local.tags
}

resource "aws_appautoscaling_target" "ingest" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.ingest.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = 4
}

resource "aws_appautoscaling_policy" "ingest_queue_depth" {
  name               = "${local.name}-ingest-queue-depth"
  service_namespace  = aws_appautoscaling_target.ingest.service_namespace
  resource_id        = aws_appautoscaling_target.ingest.resource_id
  scalable_dimension = aws_appautoscaling_target.ingest.scalable_dimension
  policy_type        = "StepScaling"
  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 1
      scaling_adjustment          = 0
    }
    step_adjustment {
      metric_interval_lower_bound = 1
      metric_interval_upper_bound = 20
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 20
      scaling_adjustment          = 4
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "ingest_queue_depth" {
  alarm_name          = "${local.name}-ingest-queue-depth"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.ingest.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_appautoscaling_policy.ingest_queue_depth.arn]
  ok_actions          = [aws_appautoscaling_policy.ingest_queue_depth.arn]
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}
