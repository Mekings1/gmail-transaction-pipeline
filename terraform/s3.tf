resource "aws_s3_bucket" "raw_emails" {
  bucket = "${local.name_prefix}-raw-emails-${local.account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "raw_emails" {
  bucket = aws_s3_bucket.raw_emails.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_emails" {
  bucket = aws_s3_bucket.raw_emails.id

  rule {
    id     = "tiered-storage"
    status = "Enabled"

    filter { prefix = "raw/" }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw_emails" {
  bucket                  = aws_s3_bucket.raw_emails.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_notification" "trigger_transform" {
  bucket = aws_s3_bucket.raw_emails.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.transform.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3_transform]
}