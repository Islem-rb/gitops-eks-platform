output "external_secrets_role_arn" {
  value = aws_iam_role.external_secrets.arn
}

output "velero_role_arn" {
  value = aws_iam_role.velero.arn
}
