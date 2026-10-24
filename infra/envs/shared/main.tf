terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "gitops-eks-platform"
      Environment = "shared"
      ManagedBy   = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "github_repo" {
  type        = string
  description = "owner/name of the GitHub repository"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repo))
    error_message = "github_repo must be owner/name (the immutable-ID form owner@id/repo@id also matches). An empty value would break the CI role trust policy."
  }
}

module "ecr" {
  source = "../../modules/ecr"

  name_prefix  = "platform"
  repositories = ["api", "worker", "frontend"]
}

module "iam" {
  source = "../../modules/iam"

  github_repo         = var.github_repo
  ecr_repository_arns = module.ecr.repository_arns
}

output "repository_urls" {
  value = module.ecr.repository_urls
}

output "ci_role_arn" {
  value = module.iam.ci_role_arn
}

output "plan_role_arn" {
  value = module.iam.plan_role_arn
}

# ---- Velero backup bucket: lives here (not in dev) so backups survive destroying the cluster ----
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "velero" {
  # checkov:skip=CKV_AWS_18: access logging for a private backup bucket is not worth the extra bucket here
  # checkov:skip=CKV_AWS_144: cross-region replication is not needed for demo backups
  # checkov:skip=CKV2_AWS_62: event notifications are not needed
  # checkov:skip=CKV_AWS_145: SSE-S3 (AES256) is enough; no KMS key to manage
  bucket = "velero-backups-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_s3_bucket_public_access_block" "velero" {
  bucket                  = aws_s3_bucket.velero.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "velero" {
  bucket = aws_s3_bucket.velero.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "velero" {
  bucket = aws_s3_bucket.velero.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "velero" {
  bucket = aws_s3_bucket.velero.id
  rule {
    id     = "expire-old-backups"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

output "velero_bucket_name" {
  value = aws_s3_bucket.velero.bucket
}
