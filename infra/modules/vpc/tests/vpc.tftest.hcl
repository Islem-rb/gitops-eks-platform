mock_provider "aws" {}

variables {
  name         = "test"
  cluster_name = "test"
}

run "default_layout_has_two_public_and_two_private_subnets" {
  command = plan

  assert {
    condition     = length(output.public_subnet_ids) == 2
    error_message = "expected 2 public subnets"
  }

  assert {
    condition     = length(output.private_subnet_ids) == 2
    error_message = "expected 2 private subnets"
  }
}
