variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "aws_account_id" {
  description = "AWS Account ID"
  type        = string
}

variable "public_subnet_ids" {
  description = "Subnets for ECS tasks"
  type        = list(string)
}

variable "ecs_security_group_id" {
  description = "Security group for ECS tasks"
  type        = string
}

variable "core_chat_worker_image_url" {
  description = "ECR URL for the core chat worker image"
  type        = string
}

variable "database_url" {
  description = "Postgres connection string"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis connection string"
  type        = string
  sensitive   = true
}
