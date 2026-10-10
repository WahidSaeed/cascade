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
  default = "16.6"
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
