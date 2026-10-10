# --------------------------------------------------------------------- Phase 7 — scale (BUILD.md §13)

# Aurora read replica: dashboard aggregates (ledger rollups) read from the reader endpoint
# (CASCADE_DATABASE_READ_URL), so heavy dashboards never compete with request writes.
resource "aws_rds_cluster_instance" "reader" {
  identifier         = "${local.name}-reader"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
  promotion_tier     = 1
  tags               = local.tags
}

# Artifact Lambdas run in the private subnets, which have NO NAT gateway and no internet route
# (aws_route_table.private has no routes besides local). The only way out is this S3 gateway endpoint,
# used by ml/dl artifacts to load their model.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = merge(local.tags, { Name = "${local.name}-s3" })
}

# Every artifact without an integration: HTTPS to S3 (through the gateway endpoint) and nothing else.
resource "aws_security_group" "artifact" {
  name        = "${local.name}-artifact"
  description = "Lambda artifacts: no outbound network except S3 via the gateway endpoint"
  vpc_id      = aws_vpc.main.id
  tags        = local.tags
}

resource "aws_vpc_security_group_egress_rule" "artifact_s3" {
  security_group_id = aws_security_group.artifact.id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "model download from S3 (gateway endpoint)"
}

resource "aws_vpc_security_group_egress_rule" "artifact_integration_s3" {
  security_group_id = aws_security_group.artifact_integration.id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "model download from S3 (gateway endpoint)"
}

# Hot exact cache: ElastiCache Serverless (Valkey) in the private subnets, reachable only from the ECS tasks.
# The database stays the source of truth (backend/app/light/hotcache.py); the app fails open without it.
resource "aws_security_group" "cache" {
  name        = "${local.name}-cache"
  description = "ElastiCache: only the ECS tasks"
  vpc_id      = aws_vpc.main.id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_svc" {
  security_group_id            = aws_security_group.cache.id
  referenced_security_group_id = aws_security_group.svc.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  description                  = "API / agent / ingest tasks"
}

resource "aws_elasticache_serverless_cache" "exact" {
  name                 = "${local.name}-exact"
  engine               = "valkey"
  major_engine_version = "8"
  subnet_ids           = aws_subnet.private[*].id
  security_group_ids   = [aws_security_group.cache.id]
  cache_usage_limits {
    data_storage {
      maximum = var.exact_cache_max_gb
      unit    = "GB"
    }
  }
  tags = local.tags
}
