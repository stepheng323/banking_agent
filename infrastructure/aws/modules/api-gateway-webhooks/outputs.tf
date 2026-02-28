output "api_id" {
  description = "HTTP API id"
  value       = aws_apigatewayv2_api.webhooks.id
}

output "api_endpoint" {
  description = "HTTP API endpoint URL"
  value       = aws_apigatewayv2_api.webhooks.api_endpoint
}

output "execution_arn" {
  description = "HTTP API execution ARN"
  value       = aws_apigatewayv2_api.webhooks.execution_arn
}
