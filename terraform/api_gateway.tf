resource "aws_apigatewayv2_api" "webhook" {
  name          = "${local.name_prefix}-webhook"
  protocol_type = "HTTP"
  description   = "Receives Gmail Pub/Sub push notifications"
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      ip             = "$context.identity.sourceIp"
    })
  }
}

resource "aws_apigatewayv2_integration" "ingestion" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingestion.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "gmail_webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook/gmail"
  target    = "integrations/${aws_apigatewayv2_integration.ingestion.id}"
}