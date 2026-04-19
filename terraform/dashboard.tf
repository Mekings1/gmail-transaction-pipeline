# ── Token ─────────────────────────────────────────────────────────────────────

resource "random_password" "dashboard_token" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "dashboard_token" {
  name        = "/gmail-txn/prod/dashboard-token"
  description = "Bearer token for the transaction dashboard URL"
  type        = "SecureString"
  value       = random_password.dashboard_token.result
  tags        = local.common_tags
}

# ── IAM ───────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "dashboard" {
  name               = "${local.name_prefix}-dashboard-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "dashboard" {
  name = "${local.name_prefix}-dashboard-policy"
  role = aws_iam_role.dashboard.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-dashboard:*"
      },
      {
        Sid      = "ReadTransactions"
        Effect   = "Allow"
        Action   = ["dynamodb:Scan","dynamodb:Query"]
        Resource = aws_dynamodb_table.transactions.arn
      },
      {
        Sid      = "ReadToken"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter/gmail-txn/*"
      }
    ]
  })
}

# ── Lambda ────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/aws/lambda/${local.name_prefix}-dashboard"
  retention_in_days = var.log_retention_days
}

data "archive_file" "dashboard" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/dashboard/dist/package"
  output_path = "${path.module}/../lambdas/dashboard/dist/dashboard.zip"
}

resource "aws_s3_object" "dashboard_zip" {
  bucket = aws_s3_bucket.raw_emails.bucket
  key    = "lambda-artifacts/dashboard.zip"
  source = data.archive_file.dashboard.output_path
  etag   = filemd5(data.archive_file.dashboard.output_path)
}

resource "aws_lambda_function" "dashboard" {
  function_name    = "${local.name_prefix}-dashboard"
  role             = aws_iam_role.dashboard.arn
  s3_bucket        = aws_s3_bucket.raw_emails.bucket
  s3_key           = aws_s3_object.dashboard_zip.key
  source_code_hash = data.archive_file.dashboard.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      DYNAMODB_TABLE_NAME   = aws_dynamodb_table.transactions.name
      DASHBOARD_TOKEN_PARAM = aws_ssm_parameter.dashboard_token.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.dashboard]
}

resource "aws_lambda_permission" "allow_api_gateway_dashboard" {
  statement_id  = "AllowAPIGatewayDashboard"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dashboard.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

# ── API Gateway routes ────────────────────────────────────────────────────────

resource "aws_apigatewayv2_integration" "dashboard" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.dashboard.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "dashboard_html" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /dashboard"
  target    = "integrations/${aws_apigatewayv2_integration.dashboard.id}"
}

resource "aws_apigatewayv2_route" "dashboard_data" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /api/data"
  target    = "integrations/${aws_apigatewayv2_integration.dashboard.id}"
}