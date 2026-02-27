output "gateway_lambda_arn" {
  description = "Gateway lambda ARN"
  value       = aws_lambda_function.gateway.arn
}

output "gateway_lambda_invoke_arn" {
  description = "Gateway lambda invoke ARN"
  value       = aws_lambda_function.gateway.invoke_arn
}

output "gateway_lambda_name" {
  description = "Gateway lambda function name"
  value       = aws_lambda_function.gateway.function_name
}
