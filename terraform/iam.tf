# ── Shared assume-role policy ─────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── Ingestion Lambda ──────────────────────────────────────────────────────────

resource "aws_iam_role" "ingestion" {
  name               = "${local.name_prefix}-ingestion-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "ingestion" {
  name = "${local.name_prefix}-ingestion-policy"
  role = aws_iam_role.ingestion.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-ingestion:*"
      },
      {
        Sid    = "ReadWriteSecret"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:gmail-txn/oauth-credentials*"
      },
      {
        Sid    = "HistoryIdState"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:PutParameter",
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter/gmail-txn/*"
      },
      {
        Sid      = "WriteRawEmails"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.raw_emails.arn}/raw/*"
      }
    ]
  })
}

# ── Transform Lambda ──────────────────────────────────────────────────────────

resource "aws_iam_role" "transform" {
  name               = "${local.name_prefix}-transform-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "transform" {
  name = "${local.name_prefix}-transform-policy"
  role = aws_iam_role.transform.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-transform:*"
      },
      {
        Sid      = "ReadRawEmails"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.raw_emails.arn}/raw/*"
      },
      {
        Sid      = "WriteTransactions"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.transactions.arn
      }
    ]
  })
}