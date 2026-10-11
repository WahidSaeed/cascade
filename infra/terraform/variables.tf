variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "cascade"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "bedrock_model_id" {
  type    = string
  default = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "light_llm_model_id" {
  description = "Small Bedrock model used only by discovery (never on the request path)."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_embed_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "container_image" {
  description = "ECR image URI:tag (set after the first push)."
  type        = string
  default     = ""
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "aurora_engine_version" {
  type    = string
  default = "16.15"
}

variable "aurora_min_acu" {
  type    = number
  default = 0.5
}

variable "aurora_max_acu" {
  type    = number
  default = 4
}

variable "skip_final_snapshot" {
  type    = bool
  default = false
}

variable "api_domain_name" {
  description = "Domain of the API (ACM certificate, HTTPS listener). Required unless api_via_cloudfront = true."
  type        = string
  default     = ""
  validation {
    condition     = length(var.api_domain_name) > 3 || var.api_domain_name == ""
    error_message = "Set api_domain_name (the API is only served over HTTPS), or set api_via_cloudfront = true."
  }
}

variable "teardown_ready" {
  description = "Set true (and apply) before `terraform destroy`: buckets and image repositories are then deleted with their contents."
  type        = bool
  default     = false
}

variable "api_via_cloudfront" {
  description = <<-EOT
    TEMPORARY / no-domain deployments: serve the API under the web distribution's own HTTPS address
    (CloudFront routes /api/* to the ALB). Browser-to-CloudFront is HTTPS; CloudFront-to-ALB is plain HTTP on :80,
    which accepts only CloudFront's origin-facing ranges. No ACM certificate or domain is needed.
    Use api_domain_name (HTTPS end to end) for anything beyond a short test.
  EOT
  type        = bool
  default     = false
}

variable "dataset_retention_days" {
  type    = number
  default = 90
}

variable "exact_cache_max_gb" {
  type        = number
  default     = 1
  description = "Storage cap for the hot exact cache (ElastiCache Serverless)."
}
