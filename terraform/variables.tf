variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-west-2"
}

variable "project_name" {
  description = "Short prefix used in all resource names"
  type        = string
  default     = "gmail-txn"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}

variable "bank_email_sender" {
  description = "Sender address of your bank alert emails"
  type        = string
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention period"
  type        = number
  default     = 14
}

variable "gmail_login_keywords" {
  description = "Comma-separated subject keywords that identify login notification emails"
  type        = string
  default     = "Login Notification"
}

variable "google_project_id" {
  description = "gmail-transaction-pipeline"
  type        = string
}

variable "pubsub_topic_name" {
  description = "Pub/Sub topic name for Gmail push notifications"
  type        = string
  default     = "gmail-transactions"
}