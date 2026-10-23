mock_provider "aws" {}

variables {
  name_prefix  = "platform"
  repositories = ["api", "worker", "frontend"]
}

run "one_repository_per_service" {
  command = plan

  assert {
    condition     = length(output.repository_arns) == 3
    error_message = "expected 3 repositories"
  }

  assert {
    condition     = toset(keys(output.repository_urls)) == toset(["api", "frontend", "worker"])
    error_message = "repository keys should be the service names"
  }
}
