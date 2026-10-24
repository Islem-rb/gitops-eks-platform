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
      Environment = "dev"
      ManagedBy   = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

locals {
  name         = "platform-dev"
  cluster_name = "platform-dev"
}

module "vpc" {
  source = "../../modules/vpc"

  name         = local.name
  cluster_name = local.cluster_name
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

variable "kubernetes_version" {
  type    = string
  default = "1.37"
}

variable "public_access_cidrs" {
  type        = list(string)
  description = "Your public IP as /32"

  validation {
    condition     = length(var.public_access_cidrs) > 0 && !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "Set public_access_cidrs in terraform.tfvars (your current IP as /32). It must not be 0.0.0.0/0."
  }
}

module "eks" {
  source = "../../modules/eks"

  cluster_name        = local.cluster_name
  kubernetes_version  = var.kubernetes_version
  private_subnet_ids  = module.vpc.private_subnet_ids
  public_access_cidrs = var.public_access_cidrs
  node_instance_types = ["m7i-flex.large"]
  node_capacity_type  = "ON_DEMAND"
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

data "aws_caller_identity" "current" {}

module "workload_identity" {
  source = "../../modules/workload-identity"

  cluster_name       = module.eks.cluster_name
  region             = var.region
  velero_bucket_name = "velero-backups-${data.aws_caller_identity.current.account_id}-${var.region}"
}

output "external_secrets_role_arn" {
  value = module.workload_identity.external_secrets_role_arn
}

output "velero_role_arn" {
  value = module.workload_identity.velero_role_arn
}
