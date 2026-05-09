terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket  = "banking-agent-tf-state-dev-use1-808537413474"
    key     = "dev/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  project_name          = "banking-agent"
  environment           = "dev"
  rds_database_url      = var.provision_rds ? "postgresql://banking_user:${var.db_password}@${module.database[0].db_endpoint}/banking_db" : ""
  secret_database_url   = trimspace(lookup(var.secret_config_values, "DATABASE_URL", ""))
  resolved_database_url = trimspace(var.database_url) != "" ? trimspace(var.database_url) : (local.secret_database_url != "" ? local.secret_database_url : local.rds_database_url)
  ssm_kms_key_alias     = var.ssm_kms_key_alias_name != "" ? var.ssm_kms_key_alias_name : "alias/${local.project_name}-${local.environment}-ssm"

  non_secret_env_vars = merge(
    {
      META_PHONE_NUMBER_ID           = var.meta_phone_number_id
      ONBOARDING_FLOW_ID             = var.onboarding_flow_id
      ACCOUNT_LINKING_FLOW_ID        = var.account_linking_flow_id
      PIN_CONFIRMATION_FLOW_ID       = var.pin_confirmation_flow_id
      TTL_SECONDS                    = var.ttl_seconds
      FLOW_SESSION_TIMEOUT           = var.flow_session_timeout
      PENDING_TRANSACTION_TTL        = var.pending_transaction_ttl
      FLUTTERWAVE_USE_SANDBOX        = var.flutterwave_use_sandbox
      S3_BUCKET_NAME                 = var.s3_bucket_name
      DEFAULT_CHANNEL                = var.default_channel
      TELEGRAM_MINI_APP_BASE_URL     = var.telegram_mini_app_base_url
      ASSISTANT_PROFILE_PATH         = var.assistant_profile_path
      CAPABILITY_POLICY_PATH         = var.capability_policy_path
      DOMAIN_GUARDRAILS_PATH         = var.domain_guardrails_path
      ENABLE_CHANNEL_OPTION_UX_V2    = var.enable_channel_option_ux_v2
      ENABLE_TRANSFER_SCHEDULING     = var.enable_transfer_scheduling
      SCHEDULE_DISPATCHER_BATCH_SIZE = var.schedule_dispatcher_batch_size
      SCHEDULE_MAX_DUE_PER_TICK      = var.schedule_max_due_per_tick
      SCHEDULE_RETRY_DELAY_MINUTES   = var.schedule_retry_delay_minutes
    },
    var.non_secret_config_values
  )

  secret_env_vars = merge(
    {
      DATABASE_URL                  = local.resolved_database_url
      OPENAI_API_KEY                = var.openai_api_key
      META_VERIFY_TOKEN             = var.meta_verify_token
      META_ACCESS_TOKEN             = var.meta_access_token
      TELEGRAM_BOT_TOKEN            = var.telegram_bot_token
      TELEGRAM_WEBHOOK_SECRET_TOKEN = var.telegram_webhook_secret_token
      MONO_API_KEY                  = var.mono_api_key
      FLUTTERWAVE_SECRET_KEY        = var.flutterwave_secret_key
    },
    var.secret_config_values
  )

  critical_secret_keys = [
    "OPENAI_API_KEY",
    "META_VERIFY_TOKEN",
    "META_ACCESS_TOKEN",
    "MONO_API_KEY",
    "FLUTTERWAVE_SECRET_KEY"
  ]

  critical_non_secret_keys = [
    "META_PHONE_NUMBER_ID",
    "ONBOARDING_FLOW_ID",
    "ACCOUNT_LINKING_FLOW_ID",
    "PIN_CONFIRMATION_FLOW_ID"
  ]

  missing_critical_secret_keys = [
    for key in local.critical_secret_keys : key
    if trimspace(lookup(local.secret_env_vars, key, "")) == ""
  ]

  missing_critical_non_secret_keys = [
    for key in local.critical_non_secret_keys : key
    if trimspace(lookup(local.non_secret_env_vars, key, "")) == ""
  ]
}

resource "aws_kms_key" "ssm_parameters" {
  description             = "CMK for SSM SecureString parameters (${local.project_name}-${local.environment})"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "ssm_parameters" {
  name          = local.ssm_kms_key_alias
  target_key_id = aws_kms_key.ssm_parameters.key_id
}

check "critical_secret_config_present" {
  assert {
    condition     = length(local.missing_critical_secret_keys) == 0
    error_message = "Missing critical secret configuration values: ${join(", ", nonsensitive(local.missing_critical_secret_keys))}"
  }
}

check "critical_non_secret_config_present" {
  assert {
    condition     = length(local.missing_critical_non_secret_keys) == 0
    error_message = "Missing critical non-secret configuration values: ${join(", ", local.missing_critical_non_secret_keys)}"
  }
}

check "whatsapp_flow_private_key_source_present" {
  assert {
    condition     = trimspace(lookup(local.secret_env_vars, "WHATSAPP_FLOW_PRIVATE_KEY", "")) != ""
    error_message = "Set WHATSAPP_FLOW_PRIVATE_KEY."
  }
}

check "database_url_present_when_rds_disabled" {
  assert {
    condition     = var.provision_rds || trimspace(local.resolved_database_url) != ""
    error_message = "DATABASE_URL must be set when provision_rds=false."
  }
}

check "database_url_not_set_when_rds_enabled" {
  assert {
    condition     = !var.provision_rds || (trimspace(var.database_url) == "" && local.secret_database_url == "")
    error_message = "When provision_rds=true, DATABASE_URL must not be set in either database_url or secret_config_values."
  }
}

check "db_password_required_when_rds_enabled" {
  assert {
    condition     = !var.provision_rds || trimspace(var.db_password) != ""
    error_message = "db_password must be set when provision_rds=true."
  }
}

check "api_gateway_throttling_values_valid" {
  assert {
    condition     = var.api_gateway_throttling_burst_limit >= 0 && var.api_gateway_throttling_rate_limit > 0
    error_message = "API Gateway throttling values must be non-negative (burst) and positive (rate)."
  }
}

module "networking" {
  source       = "./modules/networking"
  project_name = local.project_name
  environment  = local.environment
  vpc_cidr     = "10.0.0.0/16"
}

module "ecr" {
  source       = "./modules/ecr"
  project_name = local.project_name
  environment  = local.environment
}

module "database" {
  count                 = var.provision_rds ? 1 : 0
  source                = "./modules/database"
  project_name          = local.project_name
  environment           = local.environment
  private_subnet_ids    = module.networking.private_subnet_ids
  rds_security_group_id = module.networking.rds_security_group_id
  db_password           = var.db_password
}

module "config_ssm" {
  source              = "./modules/config-ssm"
  project_name        = local.project_name
  environment         = local.environment
  secret_env_vars     = local.secret_env_vars
  non_secret_env_vars = local.non_secret_env_vars
  ssm_kms_key_arn     = aws_kms_key.ssm_parameters.arn
  overwrite           = true
}

module "compute" {
  source                     = "./modules/compute-ecs"
  project_name               = local.project_name
  environment                = local.environment
  aws_region                 = var.aws_region
  aws_account_id             = data.aws_caller_identity.current.account_id
  public_subnet_ids          = module.networking.public_subnet_ids
  ecs_security_group_id      = module.networking.ecs_security_group_id
  chat_worker_image_url      = "${module.ecr.core_repository_url}:chat-worker-latest"
  non_secret_env_vars        = local.non_secret_env_vars
  secret_parameter_arns      = module.config_ssm.secret_parameter_arns
  all_parameter_arns         = module.config_ssm.all_parameter_arns
  ssm_kms_key_arn            = aws_kms_key.ssm_parameters.arn
  sns_topic_arn              = module.messaging.sns_topic_arn
}

module "messaging" {
  source       = "./modules/messaging"
  project_name = local.project_name
  environment  = local.environment
}

module "compute_lambda_events" {
  source                = "./modules/compute-lambda-events"
  project_name          = local.project_name
  environment           = local.environment
  aws_region            = var.aws_region
  aws_account_id        = data.aws_caller_identity.current.account_id
  non_secret_env_vars   = local.non_secret_env_vars
  secret_env_vars       = local.secret_env_vars
  all_parameter_arns    = module.config_ssm.all_parameter_arns
  ssm_kms_key_arn       = aws_kms_key.ssm_parameters.arn
  queue_arns            = module.messaging.queue_arns
  sns_topic_arn         = module.messaging.sns_topic_arn
  log_retention_in_days = var.lambda_log_retention_in_days
  batch_size_by_queue = {
    "transactions" = 5
    "receipts"     = 1
  }
  batch_window_by_queue = {
    "transactions" = 2
    "receipts"     = 0
  }
  max_concurrency_by_queue = {
    "receipts" = 10
  }
  enable_schedule_dispatcher   = var.enable_schedule_dispatcher
  schedule_dispatch_expression = var.schedule_dispatch_expression

  worker_lambda_image_urls = {
    "transaction-worker" = "${module.ecr.core_repository_url}:lambda-transaction-latest"
    "receipt-worker"     = "${module.ecr.core_repository_url}:lambda-receipt-latest"
  }
}

module "compute_lambda_gateway" {
  source                   = "./modules/compute-lambda-gateway"
  project_name             = local.project_name
  environment              = local.environment
  aws_region               = var.aws_region
  aws_account_id           = data.aws_caller_identity.current.account_id
  gateway_lambda_image_url = "${module.ecr.gateway_repository_url}:lambda-latest"
  non_secret_env_vars      = local.non_secret_env_vars
  secret_env_vars          = local.secret_env_vars
  all_parameter_arns       = module.config_ssm.all_parameter_arns
  ssm_kms_key_arn          = aws_kms_key.ssm_parameters.arn
  sns_topic_arn            = module.messaging.sns_topic_arn
  log_retention_in_days    = var.lambda_log_retention_in_days
}

module "api_gateway_webhooks" {
  source                       = "./modules/api-gateway-webhooks"
  project_name                 = local.project_name
  environment                  = local.environment
  lambda_function_name         = module.compute_lambda_gateway.gateway_lambda_name
  lambda_invoke_arn            = module.compute_lambda_gateway.gateway_lambda_invoke_arn
  access_log_retention_in_days = var.api_gateway_access_log_retention_in_days
  throttling_burst_limit       = var.api_gateway_throttling_burst_limit
  throttling_rate_limit        = var.api_gateway_throttling_rate_limit
}
