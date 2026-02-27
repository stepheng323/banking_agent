output "vpc_id" {
  value = module.networking.vpc_id
}

output "core_ecr_repository" {
  value = module.ecr.core_repository_url
}

output "gateway_ecr_repository" {
  value = module.ecr.gateway_repository_url
}

output "database_endpoint" {
  value = length(module.database) > 0 ? module.database[0].db_endpoint : null
}

output "async_worker_lambda_names" {
  value = module.compute_lambda_events.worker_lambda_names
}

output "gateway_lambda_name" {
  value = module.compute_lambda_gateway.gateway_lambda_name
}

output "webhook_api_endpoint" {
  value = module.api_gateway_webhooks.api_endpoint
}

output "core_chat_worker_service_name" {
  value = module.compute.core_chat_worker_service_name
}
