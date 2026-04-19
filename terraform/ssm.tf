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