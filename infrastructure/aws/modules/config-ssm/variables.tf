variable "project_name" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "secret_env_vars" {
  description = "Secret environment variables keyed by env var name."
  type        = map(string)
}

variable "non_secret_env_vars" {
  description = "Non-secret environment variables keyed by env var name."
  type        = map(string)
  default     = {}
}

variable "overwrite" {
  description = "Whether updates to parameter values should overwrite existing parameters."
  type        = bool
  default     = true
}
