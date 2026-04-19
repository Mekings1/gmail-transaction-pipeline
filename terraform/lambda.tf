# ── Build artifacts (stub handlers for Phase 2) ───────────────────────────────

data "archive_file" "ingestion" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/ingestion/dist/package"
  output_path = "${path.module}/../lambdas/ingestion/dist/ingestion.zip"
}

data "archive_file" "transform" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/transform/dist/package"
  output_path = "${path.module}/../lambdas/transform/dist/transform.zip"
}

# s3 upload resources to by-pass direct upload to lambda

resource "aws_s3_object" "ingestion_zip" {
  bucket = aws_s3_bucket.raw_emails.bucket
  key    = "lambda-artifacts/ingestion.zip"
  source = data.archive_file.ingestion.output_path
  etag   = filemd5(data.archive_file.ingestion.output_path)
}

resource "aws_s3_object" "transform_zip" {
  bucket = aws_s3_bucket.raw_emails.bucket
  key    = "lambda-artifacts/transform.zip"
  source = data.archive_file.transform.output_path
  etag   = filemd5(data.archive_file.transform.output_path)
}

# ── Log groups (created before functions so retention applies from day one) ───

resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/aws/lambda/${local.name_prefix}-ingestion"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "transform" {
  name              = "/aws/lambda/${local.name_prefix}-transform"
  retention_in_days = var.log_retention_days
}

# ── Ingestion Lambda ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "ingestion" {
  function_name    = "${local.name_prefix}-ingestion"
  role             = aws_iam_role.ingestion.arn
  s3_bucket        = aws_s3_bucket.raw_emails.bucket
  s3_key           = aws_s3_object.ingestion_zip.key
  source_code_hash = data.archive_file.ingestion.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      RAW_BUCKET_NAME       = aws_s3_bucket.raw_emails.bucket
      SECRET_NAME           = "gmail-txn/oauth-credentials"
      BANK_EMAIL_SENDER     = var.bank_email_sender
      PUBSUB_TOPIC          = "projects/${var.google_project_id}/topics/${var.pubsub_topic_name}"
      GMAIL_LOGIN_KEYWORDS  = var.gmail_login_keywords
      HISTORY_ID_PARAM      = "/gmail-txn/prod/history-id"
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingestion]
}

# ── Transform Lambda ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "transform" {
  function_name    = "${local.name_prefix}-transform"
  role             = aws_iam_role.transform.arn
  s3_bucket        = aws_s3_bucket.raw_emails.bucket
  s3_key           = aws_s3_object.transform_zip.key
  source_code_hash = data.archive_file.transform.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.transactions.name
      BANK_EMAIL_SENDER   = var.bank_email_sender
    }
  }

  depends_on = [aws_cloudwatch_log_group.transform]
}

# ── Lambda Permissions ────────────────────────────────────────────────────────

resource "aws_lambda_permission" "allow_api_gateway_ingestion" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

resource "aws_lambda_permission" "allow_s3_transform" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transform.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_emails.arn
}