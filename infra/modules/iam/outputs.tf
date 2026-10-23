output "ci_role_arn" {
  value = aws_iam_role.ci.arn
}

output "plan_role_arn" {
  value = aws_iam_role.plan.arn
}
