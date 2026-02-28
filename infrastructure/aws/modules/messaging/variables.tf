variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "topics" {
  description = "List of SNS topics to create"
  type        = list(string)
  default = [
    "notification-send",
    "flow-event-process",
    "transaction-execute",
    "funding-process",
    "payout-process",
    "refund-process",
    "receipt-process",
    "actionable-message-send",
    "message-received"
  ]
}

variable "queues" {
  description = "Map of SNS topics to their primary SQS queue names"
  type        = map(string)
  default = {
    "notification-send"       = "banking-outbox"
    "flow-event-process"      = "banking-flow-events"
    "transaction-execute"     = "banking-transactions"
    "funding-process"         = "banking-funding"
    "payout-process"          = "banking-payouts"
    "refund-process"          = "banking-refunds"
    "receipt-process"         = "banking-receipt-jobs"
    "actionable-message-send" = "banking-actionable-messages"
    "message-received"        = "banking-messages"
  }
}

variable "default_visibility_timeout_seconds" {
  description = "Default SQS visibility timeout for all queues"
  type        = number
  default     = 60
}

variable "visibility_timeout_by_queue" {
  description = "Per-queue visibility timeout overrides (keyed by topic key)"
  type        = map(number)
  default = {
    "message-received"        = 30
    "flow-event-process"      = 30
    "transaction-execute"     = 180
    "funding-process"         = 180
    "payout-process"          = 180
    "refund-process"          = 180
    "notification-send"       = 150
    "actionable-message-send" = 150
    "receipt-process"         = 150
  }
}

variable "dlq_max_receive_count" {
  description = "Number of receives before moving message to DLQ"
  type        = number
  default     = 5
}
