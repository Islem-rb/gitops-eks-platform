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
