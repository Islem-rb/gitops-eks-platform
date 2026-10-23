mock_provider "aws" {
  # the mock would otherwise return a random string, which is not a valid IAM policy
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:AssumeRole\",\"Resource\":\"*\"}]}"
    }
  }
}

variables {
  github_repo         = "Owner@1/repo@2"
  ecr_repository_arns = ["arn:aws:ecr:eu-central-1:123456789012:repository/platform/api"]
}

run "accepts_immutable_id_repo_name" {
  command = plan

  assert {
    condition     = aws_iam_role.ci.name == "platform-github-ci"
    error_message = "CI role name changed"
  }

  assert {
    condition     = aws_iam_role.plan.name == "platform-github-plan"
    error_message = "plan role name changed"
  }
}

# regression test for the near-miss where an empty github_repo would have broken the trust policy
run "rejects_empty_github_repo" {
  command = plan

  variables {
    github_repo = ""
  }

  expect_failures = [var.github_repo]
}

run "rejects_repo_without_owner_and_name" {
  command = plan

  variables {
    github_repo = "just-a-name"
  }

  expect_failures = [var.github_repo]
}
