variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "sns_topic_name" {
  description = "Base name for the consolidated async-jobs SNS topic"
  type        = string
  default     = "async-jobs"
}

variable "queues" {
  description = "Map of queue keys to their primary SQS queue names"
  type        = map(string)
  default = {
    "transactions" = "banking-transactions"
    "receipts"     = "banking-receipts"
  }
}

variable "default_visibility_timeout_seconds" {
  description = "Default SQS visibility timeout for all queues"
  type        = number
  default     = 60
}

variable "visibility_timeout_by_queue" {
  description = "Per-queue visibility timeout overrides"
  type        = map(number)
  default = {
    "transactions" = 180
    "receipts"     = 150
  }
}

variable "dlq_max_receive_count" {
  description = "Number of receives before moving message to DLQ"
  type        = number
  default     = 5
}

variable "queue_filter_policies" {
  description = "SNS subscription filter policies per queue (jsonencode'd)"
  type        = map(string)
  default = {
    "transactions" = "{\"domain\":[\"transaction\",\"funding\",\"payout\",\"refund\"]}"
    "receipts"     = "{\"domain\":[\"receipt\"]}"
  }
}
