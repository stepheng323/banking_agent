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

variable "non_secret_env_vars" {
  description = "Map of non-secret env var names to their values."
  type        = map(string)
}

variable "secret_parameter_arns" {
  description = "Map of secret env var names to SSM parameter ARNs."
  type        = map(string)
}

variable "all_parameter_arns" {
  description = "Map of all env var names to SSM parameter ARNs for IAM scoping."
  type        = map(string)
}

variable "ssm_kms_key_arn" {
  description = "KMS key ARN used for SSM SecureString decryption."
  type        = string
}
