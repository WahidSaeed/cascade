output "ecr_repository_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "Push the API image here (DEPLOY.md)."
}

output "alb_url" {
  value = "http://${aws_lb.api.dns_name}"
}

output "aurora_endpoint" {
  value = aws_rds_cluster.main.endpoint
}

output "db_secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "artifact_role_arn" {
  value = aws_iam_role.artifact.arn
}

output "runtime_repository_urls" {
  value = { for k, r in aws_ecr_repository.runtime : k => r.repository_url }
}

output "training_repository_url" {
  value = aws_ecr_repository.training.repository_url
}

output "web_distribution_id" {
  value = aws_cloudfront_distribution.web.id
}
