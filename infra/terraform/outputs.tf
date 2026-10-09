output "ecr_repository_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "Push the API image here (see scripts/push.sh)."
}

output "alb_url" {
  value       = "http://${aws_lb.api.dns_name}"
  description = "Public URL of the Cascade API/UI once the service is healthy."
}

output "dynamodb_tables" {
  value = {
    patterns = aws_dynamodb_table.patterns.name
    cache    = aws_dynamodb_table.cache.name
    logs     = aws_dynamodb_table.logs.name
  }
}

output "handlers_bucket" {
  value = aws_s3_bucket.handlers.bucket
}
