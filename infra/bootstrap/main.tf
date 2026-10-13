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
      Project   = "gitops-eks-platform"
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
}

# The bucket that stores Terraform state
resource "aws_s3_bucket" "state" {
  bucket = local.bucket_name

  # checkov:skip=CKV_AWS_18: access logging needs a second bucket; not worth it for a personal state bucket
  # checkov:skip=CKV_AWS_144: cross-region replication is overkill for a single-account learning project
  # checkov:skip=CKV_AWS_145: SSE-S3 (AES256) is enough here; a KMS key adds cost and complexity
  # checkov:skip=CKV2_AWS_62: no consumers need event notifications for state files

  lifecycle {
    prevent_destroy = true # protects against accidental deletion
  }
}

# Keep every version of the state file, so we can recover from mistakes
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt the state at rest (it can contain secrets)
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Never allow public access
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Delete old state versions after 90 days to keep it tidy
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

output "state_bucket" {
  value = aws_s3_bucket.state.id
}
