# PLAN-ONLY environment: this stack is never applied. CI only runs terraform validate on it.
# It documents how staging would differ from dev (own CIDR, sizing, state key).
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
      Environment = "staging"
      ManagedBy   = "terraform"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "kubernetes_version" {
  type    = string
  default = "1.37"
}

variable "public_access_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the public API endpoint (no default on purpose)"

  validation {
    condition     = length(var.public_access_cidrs) > 0 && !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "public_access_cidrs must be non-empty and must not open the API endpoint to 0.0.0.0/0."
  }
}

locals {
  name         = "platform-staging"
  cluster_name = "platform-staging"
}

module "vpc" {
  source = "../../modules/vpc"

  name         = local.name
  cluster_name = local.cluster_name
  cidr         = "10.1.0.0/16"
}

module "eks" {
  source = "../../modules/eks"

  cluster_name        = local.cluster_name
  kubernetes_version  = var.kubernetes_version
  private_subnet_ids  = module.vpc.private_subnet_ids
  public_access_cidrs = var.public_access_cidrs
  node_instance_types = ["t3.medium"]
  node_capacity_type  = "SPOT"
  node_desired_size   = 2
  node_min_size       = 1
  node_max_size       = 3
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "cluster_name" {
  value = module.eks.cluster_name
}
