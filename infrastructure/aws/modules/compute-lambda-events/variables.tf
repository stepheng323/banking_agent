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

variable "worker_lambda_image_urls" {
  description = "ECR image URLs keyed by worker name (transaction-worker, messaging-worker)"
  type        = map(string)

  validation {
    condition = alltrue([
      contains(keys(var.worker_lambda_image_urls), "transaction-worker"),
      contains(keys(var.worker_lambda_image_urls), "messaging-worker"),
    ])
    error_message = "worker_lambda_image_urls must include keys: transaction-worker and messaging-worker."
  }
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
