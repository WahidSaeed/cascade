# --------------------------------------------------------------------- Phase 6: auth, HTTPS, web hosting, retention
# Cognito: identity for the API (CASCADE_AUTH=cognito); the `admins` group may approve and configure.
resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }
  tags = local.tags
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = ["https://${aws_cloudfront_distribution.web.domain_name}/auth/callback/"]
  logout_urls                          = ["https://${aws_cloudfront_distribution.web.domain_name}/"]
  supported_identity_providers         = ["COGNITO"]
}

# The hosted UI the web app signs in through (authorization code + PKCE).
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${local.name}-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_group" "groups" {
  for_each     = toset(["admins", "support", "managers", "hr"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.main.id
}

# HTTPS on the ALB: :443 with the ACM certificate; :80 only redirects (bearer tokens never travel in clear,
# and the HTTPS CloudFront page can call the API without mixed content).
resource "aws_acm_certificate" "api" {
  count             = var.api_via_cloudfront ? 0 : 1
  domain_name       = var.api_domain_name
  validation_method = "DNS"
  tags              = local.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener" "https" {
  count             = var.api_via_cloudfront ? 0 : 1
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.api[0].arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_security_group_rule" "alb_https" {
  count             = var.api_via_cloudfront ? 0 : 1
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.alb.id
}

# :80 redirects for everyone (HTTPS mode), or forwards for CloudFront only (api_via_cloudfront)
resource "aws_security_group_rule" "alb_http_redirect" {
  count             = var.api_via_cloudfront ? 0 : 1
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.alb.id
}

resource "aws_security_group_rule" "alb_from_cloudfront" {
  count             = var.api_via_cloudfront ? 1 : 0
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  prefix_list_ids   = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  security_group_id = aws_security_group.alb.id
}

# Web: the static export of web/ in a private bucket behind CloudFront (origin access control).
resource "aws_s3_bucket" "web" {
  bucket        = "${local.name}-web-${local.account_id}"
  force_destroy = var.teardown_ready
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The static export writes /patterns/index.html etc.; S3 (behind OAC) has no directory indexes, so map
# "/patterns/" -> "/patterns/index.html" (and "/patterns" -> "/patterns/index.html") at the edge.
resource "aws_cloudfront_function" "index" {
  name    = "${local.name}-index"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var req = event.request;
      if (req.uri.endsWith("/")) { req.uri += "index.html"; }
      else if (req.uri.split("/").pop().indexOf(".") === -1) { req.uri += "/index.html"; }
      return req;
    }
  EOT
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }
  dynamic "origin" {
    for_each = var.api_via_cloudfront ? [1] : []
    content {
      domain_name = aws_lb.api.dns_name
      origin_id   = "api"
      custom_origin_config {
        http_port                = 80
        https_port               = 443
        origin_protocol_policy   = "http-only"
        origin_ssl_protocols     = ["TLSv1.2"]
        origin_read_timeout      = 60
        origin_keepalive_timeout = 60
      }
    }
  }
  dynamic "ordered_cache_behavior" {
    for_each = var.api_via_cloudfront ? [1] : []
    content {
      path_pattern             = "/api/*"
      target_origin_id         = "api"
      viewer_protocol_policy   = "https-only"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = false                                  # keep SSE streams unbuffered
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # Managed-AllViewerExceptHostHeader
    }
  }
  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.index.arn
    }
    allowed_methods = ["GET", "HEAD"]
    cached_methods  = ["GET", "HEAD"]
    compress        = true
    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  tags = local.tags
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json
}

# Retention: datasets expire, old document versions are pruned (rule 6).
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "datasets"
    status = "Enabled"
    filter {
      prefix = "datasets/"
    }
    expiration {
      days = var.dataset_retention_days
    }
  }
  rule {
    id     = "uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    expiration {
      days = 7
    }
  }
  rule {
    id     = "old-versions"
    status = "Enabled"
    filter {
      prefix = "kb/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_cloudwatch_event_rule" "retention" {
  name                = "${local.name}-retention"
  schedule_expression = "cron(30 2 * * ? *)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "retention" {
  rule     = aws_cloudwatch_event_rule.retention.name
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
  input = jsonencode({ containerOverrides = [{ name = "jobs", command = ["python", "-m", "app.retention"] }] })
}

output "web_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_domain" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "api_url" {
  value = var.api_via_cloudfront ? "https://${aws_cloudfront_distribution.web.domain_name}" : "https://${var.api_domain_name}"
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}
