resource "aws_ssm_parameter" "history_id" {
  name        = "/gmail-txn/prod/history-id"
  description = "Gmail historyId cursor for the ingestion Lambda"
  type        = "String"
  value       = "0"   # placeholder — gmail_watch.py sets the real value

  lifecycle {
    ignore_changes = [value]  # Terraform never overwrites after initial creation
  }

  tags = local.common_tags
}

resource "aws_ssm_parameter" "oauth_credentials" {
  name        = "/gmail-txn/prod/oauth-credentials"
  description = "Gmail OAuth credentials for ingestion Lambda"
  type        = "SecureString"
  value       = "{}"   # placeholder — gmail_watch.py writes the real value

  lifecycle {
    ignore_changes = [value]
  }

  tags = local.common_tags
}