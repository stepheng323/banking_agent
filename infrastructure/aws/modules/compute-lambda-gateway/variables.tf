variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account id"
  type        = string
}

variable "gateway_lambda_image_url" {
  description = "ECR image URL used by gateway lambda"
  type        = string
}

variable "database_url" {
  description = "Database URL"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Redis URL"
  type        = string
  sensitive   = true
}

variable "topic_arns" {
  description = "SNS topic ARN map from messaging module"
  type        = map(string)
}
