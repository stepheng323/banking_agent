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
