# --------------------------------------------------------------------- ECS roles
data "aws_iam_policy_document" "ecs_assume" {
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
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role injects the database secret into the task.
data "aws_iam_policy_document" "exec_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db.arn]
  }
}

resource "aws_iam_role_policy" "exec_secrets" {
  name   = "${local.name}-exec-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.exec_secrets.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid     = "Bedrock"
    actions = ["bedrock:InvokeModel", "bedrock:Converse"]
    # Inference profiles route to the underlying foundation model in any region of the profile.
    resources = [
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.light_llm_model_id}",
      "arn:aws:bedrock:*::foundation-model/${replace(var.bedrock_model_id, "/^(us|eu|apac|global)\\./", "")}",
      "arn:aws:bedrock:*::foundation-model/${replace(var.light_llm_model_id, "/^(us|eu|apac|global)\\./", "")}",
      "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_embed_model_id}",
    ]
  }
  statement {
    sid       = "Artifacts"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    sid = "ArtifactLambdas"
    actions = ["lambda:CreateFunction", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
    "lambda:DeleteFunction", "lambda:GetFunction", "lambda:InvokeFunction", "lambda:TagResource"]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:${local.name}-artifact-*"]
  }
  statement {
    sid       = "PassArtifactRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.artifact.arn]
  }
  statement {
    sid       = "PiiScreening"
    actions   = ["comprehend:DetectPiiEntities"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${local.name}-task-policy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --------------------------------------------------------------------- artifact execution role
# Every approved artifact runs as its own Lambda with this role: CloudWatch logs only.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "artifact" {
  name               = "${local.name}-artifact"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "artifact_basic" {
  role       = aws_iam_role.artifact.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
