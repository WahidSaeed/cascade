# --------------------------------------------------------------------- Aurora PostgreSQL Serverless v2
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db" {
  name = "${local.name}/db"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = "cascade"
    password = random_password.db.result
    url      = "postgresql+psycopg://cascade:${random_password.db.result}@${aws_rds_cluster.main.endpoint}:5432/cascade"
    read_url = "postgresql+psycopg://cascade:${random_password.db.result}@${aws_rds_cluster.main.reader_endpoint}:5432/cascade"
  })
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

resource "aws_rds_cluster_parameter_group" "main" {
  name   = "${local.name}-pg16"
  family = "aurora-postgresql16"
  # pgvector 0.8 iterative index scans (hnsw.iterative_scan) are enabled per session by the app.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  tags = local.tags
}

resource "aws_rds_cluster" "main" {
  cluster_identifier              = local.name
  engine                          = "aurora-postgresql"
  engine_mode                     = "provisioned"
  engine_version                  = var.aurora_engine_version
  database_name                   = "cascade"
  master_username                 = "cascade"
  master_password                 = random_password.db.result
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.main.name
  storage_encrypted               = true
  backup_retention_period         = 7
  skip_final_snapshot             = var.skip_final_snapshot
  final_snapshot_identifier       = "${local.name}-final"
  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_acu
    max_capacity = var.aurora_max_acu
  }
  tags = local.tags
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name}-writer"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
  tags               = local.tags
}

# --------------------------------------------------------------------- S3 (artifacts, documents, datasets)
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.name}-artifacts-${local.account_id}"
  force_destroy = false
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
