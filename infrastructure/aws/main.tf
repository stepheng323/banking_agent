terraform {
  required_version = ">= 1.6.0"

  backend "s3" {
    # must run backend-setup.tf first, then provide their bucket/table names below
    bucket         = "banking-agent-tf-state-dev"
    key            = "dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "banking-agent-tf-locks-dev"
    encrypt        = true
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
  resolved_database_url = var.database_url != "" ? var.database_url : local.rds_database_url

  non_secret_env_vars = merge(
    {
      META_PHONE_NUMBER_ID           = ""
      WHATSAPP_FLOW_PRIVATE_KEY_PATH = ""
      ONBOARDING_FLOW_ID             = ""
      ACCOUNT_LINKING_FLOW_ID        = ""
      PIN_CONFIRMATION_FLOW_ID       = ""
      TTL_SECONDS                    = "6000"
      FLOW_SESSION_TIMEOUT           = "600"
      PENDING_TRANSACTION_TTL        = "300"
      FLUTTERWAVE_USE_SANDBOX        = "false"
      S3_BUCKET_NAME                 = ""
      DEFAULT_CHANNEL                = "whatsapp"
      TELEGRAM_MINI_APP_BASE_URL     = ""
      SOUL_POLICY_PATH               = "config/soul_policy.json"
      ENABLE_CHANNEL_OPTION_UX_V2    = "false"
    },
    var.non_secret_config_values
  )

  secret_env_vars = merge(
    {
      DATABASE_URL                  = local.resolved_database_url
      REDIS_URL                     = var.redis_url
      OPENAI_API_KEY                = ""
      META_VERIFY_TOKEN             = ""
      META_ACCESS_TOKEN             = ""
      TELEGRAM_BOT_TOKEN            = ""
      TELEGRAM_WEBHOOK_SECRET_TOKEN = ""
      MONO_API_KEY                  = ""
      FLUTTERWAVE_SECRET_KEY        = ""
    },
    var.secret_config_values
  )
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
  core_chat_worker_image_url = "${module.ecr.core_repository_url}:chat-worker-latest"
  non_secret_parameter_names = module.config_ssm.non_secret_parameter_names
  secret_parameter_arns      = module.config_ssm.secret_parameter_arns
  all_parameter_arns         = module.config_ssm.all_parameter_arns
}

module "messaging" {
  source       = "./modules/messaging"
  project_name = local.project_name
  environment  = local.environment
}

module "compute_lambda_events" {
  source                     = "./modules/compute-lambda-events"
  project_name               = local.project_name
  environment                = local.environment
  aws_region                 = var.aws_region
  aws_account_id             = data.aws_caller_identity.current.account_id
  core_lambda_image_url      = "${module.ecr.core_repository_url}:lambda-workers-latest"
  non_secret_parameter_names = module.config_ssm.non_secret_parameter_names
  secret_parameter_names     = module.config_ssm.secret_parameter_names
  all_parameter_arns         = module.config_ssm.all_parameter_arns
  queue_arns                 = module.messaging.queue_arns
}

module "compute_lambda_gateway" {
  source                     = "./modules/compute-lambda-gateway"
  project_name               = local.project_name
  environment                = local.environment
  aws_region                 = var.aws_region
  aws_account_id             = data.aws_caller_identity.current.account_id
  gateway_lambda_image_url   = "${module.ecr.gateway_repository_url}:lambda-latest"
  non_secret_parameter_names = module.config_ssm.non_secret_parameter_names
  secret_parameter_names     = module.config_ssm.secret_parameter_names
  all_parameter_arns         = module.config_ssm.all_parameter_arns
  topic_arns                 = module.messaging.topic_arns
}

module "api_gateway_webhooks" {
  source               = "./modules/api-gateway-webhooks"
  project_name         = local.project_name
  environment          = local.environment
  lambda_function_name = module.compute_lambda_gateway.gateway_lambda_name
  lambda_invoke_arn    = module.compute_lambda_gateway.gateway_lambda_invoke_arn
}

module "github_oidc" {
  count             = var.github_repo_owner != "" && var.github_repo_name != "" ? 1 : 0
  source            = "./modules/github-oidc"
  project_name      = local.project_name
  environment       = local.environment
  github_repo_owner = var.github_repo_owner
  github_repo_name  = var.github_repo_name
}
