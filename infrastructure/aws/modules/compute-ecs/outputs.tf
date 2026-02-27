output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "core_service_name" {
  description = "Core chat worker service name"
  value       = aws_ecs_service.core.name
}

output "core_chat_worker_service_name" {
  description = "Core chat worker service name"
  value       = aws_ecs_service.core.name
}
