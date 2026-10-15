resource "aws_ecr_repository" "this" {
  for_each = toset(var.repositories)

  # checkov:skip=CKV_AWS_136: AES256 encryption is enough for demo images; KMS adds cost and complexity
  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE" # a tag can never be overwritten, so a deployed version is reproducible

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last ${var.keep_last_images} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.keep_last_images
      }
      action = { type = "expire" }
    }]
  })
}
