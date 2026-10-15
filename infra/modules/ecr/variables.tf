variable "name_prefix" {
  type        = string
  description = "Prefix for repository names, e.g. platform"
}

variable "repositories" {
  type        = list(string)
  description = "Repository names, e.g. [\"api\", \"worker\", \"frontend\"]"
}

variable "keep_last_images" {
  type    = number
  default = 10
}
