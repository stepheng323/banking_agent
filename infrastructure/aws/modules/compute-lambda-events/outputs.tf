output "worker_lambda_arns" {
  description = "ARNs of async worker lambdas"
  value       = { for k, v in aws_lambda_function.workers : k => v.arn }
}

output "worker_lambda_names" {
  description = "Names of async worker lambdas"
  value       = { for k, v in aws_lambda_function.workers : k => v.function_name }
}
