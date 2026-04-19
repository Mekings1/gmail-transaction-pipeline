output "webhook_url" {
  description = "Paste this into your Pub/Sub push subscription endpoint"
  value       = "${aws_apigatewayv2_api.webhook.api_endpoint}/webhook/gmail"
}

output "s3_bucket_name" {
  value = aws_s3_bucket.raw_emails.bucket
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.transactions.name
}

output "ingestion_lambda_name" {
  value = aws_lambda_function.ingestion.function_name
}

output "transform_lambda_name" {
  value = aws_lambda_function.transform.function_name
}

output "dashboard_url" {
  description = "Bookmark this URL — share with no one"
  value       = "${aws_apigatewayv2_api.webhook.api_endpoint}/dashboard?token=${random_password.dashboard_token.result}"
  sensitive   = true
}