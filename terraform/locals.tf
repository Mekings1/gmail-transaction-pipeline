locals {
  account_id   = data.aws_caller_identity.current.account_id
  name_prefix  = "${var.project_name}-${var.environment}"

  common_tags = {
    Environment = var.environment
  }
}