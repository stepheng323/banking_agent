variable "aws_region" {
  description = "AWS region to deploy to"
  type        = string
  default     = "us-east-1" # Or your preferred region
}

variable "ssm_kms_key_alias_name" {
  description = "Optional override for the SSM CMK alias (must start with alias/)."
  type        = string
  default     = ""

  validation {
    condition     = var.ssm_kms_key_alias_name == "" || startswith(var.ssm_kms_key_alias_name, "alias/")
    error_message = "ssm_kms_key_alias_name must be empty or begin with alias/."
  }
}

variable "db_password" {
  description = "Password for the RDS Postgres database"
  type        = string
  sensitive   = true
  default     = ""
}

variable "redis_url" {
  description = "Deprecated: use secret_config_values[\"REDIS_URL\"] instead."
  type        = string
  sensitive   = true
  default     = ""
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
  description = "Optional override for DATABASE_URL. Prefer secret_config_values[\"DATABASE_URL\"] for CI/local parity."
  type        = string
  sensitive   = true
  default     = ""
}

variable "provision_rds" {
  description = "Whether to provision RDS in this environment"
  type        = bool
  default     = false
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "meta_verify_token" {
  description = "Meta webhook verify token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "meta_access_token" {
  description = "Meta access token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "telegram_bot_token" {
  description = "Telegram bot token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "telegram_webhook_secret_token" {
  description = "Telegram webhook secret token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "mono_api_key" {
  description = "Mono API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "mono_webhook_secret" {
  description = "Mono webhook verification secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "flutterwave_secret_key" {
  description = "Flutterwave secret key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "flutterwave_webhook_secret_hash" {
  description = "Flutterwave webhook secret hash"
  type        = string
  sensitive   = true
  default     = ""
}

variable "meta_phone_number_id" {
  description = "Meta phone number ID"
  type        = string
  default     = ""
}

variable "onboarding_flow_id" {
  description = "WhatsApp onboarding flow ID"
  type        = string
  default     = ""
}

variable "account_linking_flow_id" {
  description = "WhatsApp account-linking flow ID"
  type        = string
  default     = ""
}

variable "pin_confirmation_flow_id" {
  description = "WhatsApp PIN confirmation flow ID"
  type        = string
  default     = ""
}

variable "ttl_seconds" {
  description = "User context TTL in seconds"
  type        = string
  default     = "6000"
}

variable "flow_session_timeout" {
  description = "Flow session timeout in seconds"
  type        = string
  default     = "600"
}

variable "pending_transaction_ttl" {
  description = "Pending transaction TTL in seconds"
  type        = string
  default     = "300"
}

variable "flutterwave_use_sandbox" {
  description = "Whether to use Flutterwave sandbox mode"
  type        = string
  default     = "false"
}

variable "s3_bucket_name" {
  description = "S3 bucket name used by the app"
  type        = string
  default     = ""
}

variable "default_channel" {
  description = "Default messaging channel"
  type        = string
  default     = "whatsapp"
}

variable "telegram_mini_app_base_url" {
  description = "Base URL for Telegram mini app"
  type        = string
  default     = ""
}

variable "assistant_profile_path" {
  description = "Path to the assistant profile file in the container"
  type        = string
  default     = "config/assistant_profile.json"
}

variable "capability_policy_path" {
  description = "Path to the capability policy file in the container"
  type        = string
  default     = "config/capability_policy.json"
}

variable "domain_guardrails_path" {
  description = "Path to the domain guardrails file in the container"
  type        = string
  default     = "config/domain_guardrails.json"
}

variable "enable_channel_option_ux_v2" {
  description = "Feature flag for channel-option UX v2"
  type        = string
  default     = "false"
}

variable "enable_transfer_scheduling" {
  description = "Feature flag for transfer scheduling actions"
  type        = string
  default     = "true"
}

variable "schedule_dispatcher_batch_size" {
  description = "Batch size for one scheduler dispatch tick."
  type        = string
  default     = "25"
}

variable "schedule_max_due_per_tick" {
  description = "Max due schedules to process per dispatcher tick."
  type        = string
  default     = "25"
}

variable "schedule_retry_delay_minutes" {
  description = "Retry delay (minutes) for failed scheduled runs."
  type        = string
  default     = "1"
}

variable "enable_schedule_dispatcher" {
  description = "Enable EventBridge-driven schedule dispatcher lambda."
  type        = bool
  default     = true
}

variable "schedule_dispatch_expression" {
  description = "EventBridge schedule expression for dispatcher."
  type        = string
  default     = "rate(1 minute)"
}

variable "lambda_log_retention_in_days" {
  description = "Retention period for Lambda log groups."
  type        = number
  default     = 7
}

variable "api_gateway_access_log_retention_in_days" {
  description = "Retention period for API Gateway access logs."
  type        = number
  default     = 7
}

variable "api_gateway_throttling_burst_limit" {
  description = "HTTP API stage burst throttling limit."
  type        = number
  default     = 100
}

variable "api_gateway_throttling_rate_limit" {
  description = "HTTP API stage steady-state throttling rate limit."
  type        = number
  default     = 50
}
