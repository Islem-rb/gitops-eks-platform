data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

# ---------------- External Secrets Operator ----------------
resource "aws_iam_role" "external_secrets" {
  name               = "${var.cluster_name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "external_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = ["arn:${data.aws_partition.current.partition}:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:${var.secret_prefix}*"]
  }
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "read-platform-secrets"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets.json
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = var.cluster_name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
}

# ---------------- Velero ----------------
resource "aws_iam_role" "velero" {
  name               = "${var.cluster_name}-velero"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "velero" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.velero_bucket_name}"]
  }
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.velero_bucket_name}/*"]
  }
}

resource "aws_iam_role_policy" "velero" {
  name   = "velero-bucket-access"
  role   = aws_iam_role.velero.id
  policy = data.aws_iam_policy_document.velero.json
}

resource "aws_eks_pod_identity_association" "velero" {
  cluster_name    = var.cluster_name
  namespace       = "velero"
  service_account = "velero-server"
  role_arn        = aws_iam_role.velero.arn
}
