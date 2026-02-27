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

variable "core_lambda_image_url" {
  description = "ECR image URL used by async lambda workers"
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

variable "queue_arns" {
  description = "Queue ARN map from messaging module, keyed by topic key"
  type        = map(string)
}

variable "batch_size_by_queue" {
  description = "Per-queue SQS batch sizes, keyed by topic key"
  type        = map(number)
  default = {
    "transaction-execute"     = 5
    "funding-process"         = 5
    "payout-process"          = 5
    "refund-process"          = 5
    "notification-send"       = 10
    "actionable-message-send" = 10
    "receipt-process"         = 5
  }
}

variable "batch_window_by_queue" {
  description = "Per-queue batching windows in seconds, keyed by topic key"
  type        = map(number)
  default = {
    "transaction-execute"     = 2
    "funding-process"         = 2
    "payout-process"          = 2
    "refund-process"          = 2
    "notification-send"       = 1
    "actionable-message-send" = 1
    "receipt-process"         = 1
  }
}
