variable "name" {
  type        = string
  description = "Name prefix for all resources"
}

variable "cluster_name" {
  type        = string
  description = "EKS cluster name, used for subnet discovery tags"
}

variable "cidr" {
  type    = string
  default = "10.0.0.0/16"
}


variable "enable_nat_gateway" {
  type        = bool
  default     = true
  description = "A single shared NAT gateway for all private subnets (cost saving)"
}

variable "azs" {
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]
  description = "Availability zones, pinned explicitly so subnets never reshuffle"
}
