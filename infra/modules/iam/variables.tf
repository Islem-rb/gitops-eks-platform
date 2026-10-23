variable "github_repo" {
  type        = string
  description = "GitHub repository allowed to assume the role, as owner/name"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repo))
    error_message = "github_repo must be owner/name (the immutable-ID form owner@id/repo@id also matches). An empty value would break the CI role trust policy."
  }
}

variable "ecr_repository_arns" {
  type        = list(string)
  description = "ECR repositories the CI role may push to"
}

variable "name" {
  type    = string
  default = "platform-github-ci"
}

variable "plan_name" {
  type    = string
  default = "platform-github-plan"
}
