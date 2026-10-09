variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "cascade"
}

variable "bedrock_model_id" {
  type    = string
  default = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "bedrock_embed_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "container_image" {
  description = "ECR image URI:tag pushed by scripts/push.sh (set after first push)."
  type        = string
  default     = ""
}

variable "desired_count" {
  type    = number
  default = 1
}
