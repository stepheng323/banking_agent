variable "aws_region" {
  description = "AWS region to deploy to"
  type        = string
  default     = "us-east-1" # Or your preferred region
}

variable "db_password" {
  description = "Password for the RDS Postgres database"
  type        = string
  sensitive   = true
  default     = ""
}

variable "redis_url" {
  description = "Connection string for Redis (Upstash or ElastiCache)"
  type        = string
  sensitive   = true
}

variable "secret_config_values" {
  description = "Secret env var values keyed by env var name, stored as SSM SecureString."
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "non_secret_config_values" {
  description = "Non-secret env var values keyed by env var name, stored as SSM String."
  type        = map(string)
  default     = {}
}

variable "database_url" {
  description = "External database URL (e.g., Neon). If provided, ECS/Lambda will use this directly."
  type        = string
  sensitive   = true
  default     = ""
}

variable "provision_rds" {
  description = "Whether to provision RDS in this environment"
  type        = bool
  default     = false
}
