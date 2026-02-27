terraform {
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

# Get current AWS account ID
data "aws_caller_identity" "current" {}

locals {
  project_name          = "banking-agent"
  environment           = "dev"
  rds_database_url      = var.provision_rds ? "postgresql://banking_user:${var.db_password}@${module.database[0].db_endpoint}/banking_db" : ""
  resolved_database_url = var.database_url != "" ? var.database_url : local.rds_database_url
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

module "compute" {
  source                     = "./modules/compute-ecs"
  project_name               = local.project_name
  environment                = local.environment
  aws_region                 = var.aws_region
  aws_account_id             = data.aws_caller_identity.current.account_id
  public_subnet_ids          = module.networking.public_subnet_ids
  ecs_security_group_id      = module.networking.ecs_security_group_id
  core_chat_worker_image_url = "${module.ecr.core_repository_url}:chat-worker-latest"
  database_url               = local.resolved_database_url
  redis_url                  = var.redis_url
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
  core_lambda_image_url = "${module.ecr.core_repository_url}:lambda-workers-latest"
  database_url          = local.resolved_database_url
  redis_url             = var.redis_url
  queue_arns            = module.messaging.queue_arns
}

module "compute_lambda_gateway" {
  source                   = "./modules/compute-lambda-gateway"
  project_name             = local.project_name
  environment              = local.environment
  aws_region               = var.aws_region
  aws_account_id           = data.aws_caller_identity.current.account_id
  gateway_lambda_image_url = "${module.ecr.gateway_repository_url}:lambda-latest"
  database_url             = local.resolved_database_url
  redis_url                = var.redis_url
  topic_arns               = module.messaging.topic_arns
}

module "api_gateway_webhooks" {
  source               = "./modules/api-gateway-webhooks"
  project_name         = local.project_name
  environment          = local.environment
  lambda_function_name = module.compute_lambda_gateway.gateway_lambda_name
  lambda_invoke_arn    = module.compute_lambda_gateway.gateway_lambda_invoke_arn
}
