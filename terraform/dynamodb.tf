resource "aws_dynamodb_table" "transactions" {
  name         = "${local.name_prefix}-transactions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "date"
  range_key    = "sk"

  attribute {
    name = "date"   # YYYY-MM-DD  e.g. "2026-05-15"
    type = "S"
  }

  attribute {
    name = "sk"     # ISO-timestamp#uuid  e.g. "2026-05-15T14:23:00Z#abc123"
    type = "S"
  }

  attribute {
    name = "transaction_type"   # "credit" | "debit"
    type = "S"
  }

  global_secondary_index {
    name            = "type-date-index"
    hash_key        = "transaction_type"
    range_key       = "date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}