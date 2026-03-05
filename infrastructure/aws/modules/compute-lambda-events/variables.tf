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
  description = "ECR image URLs keyed by worker name (transaction-worker, receipt-worker)"
  type        = map(string)

  validation {
    condition = alltrue([
      contains(keys(var.worker_lambda_image_urls), "transaction-worker"),
      contains(keys(var.worker_lambda_image_urls), "receipt-worker"),
    ])
    error_message = "worker_lambda_image_urls must include keys: transaction-worker and receipt-worker."
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

variable "ssm_kms_key_arn" {
  description = "KMS key ARN used for SSM SecureString decryption."
  type        = string
}

variable "queue_arns" {
  description = "Queue ARN map from messaging module, keyed by queue key (transactions, receipts)"
  type        = map(string)
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for async job publishing (used by FundingConsumer)"
  type        = string
}

variable "batch_size_by_queue" {
  description = "Per-queue SQS batch sizes"
  type        = map(number)
  default = {
    "transactions" = 5
    "receipts"     = 1
  }
}

variable "batch_window_by_queue" {
  description = "Per-queue batching windows in seconds"
  type        = map(number)
  default = {
    "transactions" = 2
    "receipts"     = 0
  }
}

variable "reserved_concurrency_by_worker" {
  description = "Optional Lambda reserved concurrency by worker name"
  type        = map(number)
  default = {
    "receipt-worker" = 10
  }
}

variable "max_concurrency_by_queue" {
  description = "Optional event source max concurrency by queue key"
  type        = map(number)
  default = {
    "receipts" = 10
  }
}

variable "log_retention_in_days" {
  description = "Retention period for worker Lambda log groups."
  type        = number
  default     = 7
}
