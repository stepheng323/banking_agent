output "core_repository_url" {
  description = "URL of the Core Agent ECR repository"
  value       = aws_ecr_repository.core.repository_url
}

output "gateway_repository_url" {
  description = "URL of the Gateway ECR repository"
  value       = aws_ecr_repository.gateway.repository_url
}
