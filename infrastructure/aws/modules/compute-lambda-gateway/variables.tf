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

variable "non_secret_env_vars" {
  description = "Map of non-secret env var names to their values."
  type        = map(string)
}

variable "secret_env_vars" {
  description = "Map of secret env var names to their values."
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

variable "sns_topic_arn" {
  description = "SNS topic ARN for publishing async jobs"
  type        = string
}

variable "log_retention_in_days" {
  description = "Retention period for gateway Lambda log group."
  type        = number
  default     = 7
}
