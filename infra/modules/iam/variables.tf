variable "github_repo" {
  type        = string
  description = "GitHub repository allowed to assume the role, as owner/name"
}

variable "ecr_repository_arns" {
  type        = list(string)
  description = "ECR repositories the CI role may push to"
}

variable "name" {
  type    = string
  default = "platform-github-ci"
}
