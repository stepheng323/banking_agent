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

variable "non_secret_parameter_names" {
  description = "Map of non-secret env var names to SSM parameter names."
  type        = map(string)
}

variable "secret_parameter_names" {
  description = "Map of secret env var names to SSM parameter names."
  type        = map(string)
}

variable "all_parameter_arns" {
  description = "Map of all env var names to SSM parameter ARNs for IAM scoping."
  type        = map(string)
}

variable "topic_arns" {
  description = "SNS topic ARN map from messaging module"
  type        = map(string)
}
