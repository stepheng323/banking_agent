variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "lambda_function_name" {
  description = "Gateway webhook lambda function name"
  type        = string
}

variable "lambda_invoke_arn" {
  description = "Gateway webhook lambda invoke ARN"
  type        = string
}

variable "access_log_retention_in_days" {
  description = "Retention period for API Gateway access logs."
  type        = number
  default     = 7
}

variable "throttling_burst_limit" {
  description = "API Gateway stage-level burst throttling limit."
  type        = number
  default     = 100
}

variable "throttling_rate_limit" {
  description = "API Gateway stage-level steady-state throttling rate limit."
  type        = number
  default     = 50
}
