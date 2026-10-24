variable "cluster_name" {
  type = string
}

variable "region" {
  type = string
}

variable "velero_bucket_name" {
  type        = string
  description = "S3 bucket (created in envs/shared) that Velero may read and write"
}

variable "secret_prefix" {
  type        = string
  default     = "platform/"
  description = "External Secrets may only read Secrets Manager secrets whose name starts with this"
}
