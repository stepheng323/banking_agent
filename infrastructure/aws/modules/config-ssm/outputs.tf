output "secret_parameter_names" {
  description = "Map of secret env var names to SSM parameter names"
  value       = { for key, p in aws_ssm_parameter.secret : key => p.name }
}

output "secret_parameter_arns" {
  description = "Map of secret env var names to SSM parameter ARNs"
  value       = { for key, p in aws_ssm_parameter.secret : key => p.arn }
}

output "non_secret_parameter_names" {
  description = "Map of non-secret env var names to SSM parameter names"
  value       = { for key, p in aws_ssm_parameter.non_secret : key => p.name }
}

output "non_secret_parameter_arns" {
  description = "Map of non-secret env var names to SSM parameter ARNs"
  value       = { for key, p in aws_ssm_parameter.non_secret : key => p.arn }
}

output "all_parameter_arns" {
  description = "Map of all env var names to SSM parameter ARNs"
  value = merge(
    { for key, p in aws_ssm_parameter.secret : key => p.arn },
    { for key, p in aws_ssm_parameter.non_secret : key => p.arn }
  )
}
