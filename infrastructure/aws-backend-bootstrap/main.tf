terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo_owner" {
  description = "The GitHub username or organization name for OIDC trust."
  type        = string
  default     = ""
}

variable "github_repo_name" {
  description = "The GitHub repository name for OIDC trust."
  type        = string
  default     = ""
}

variable "create_github_actions_role" {
  description = "When true, this stack manages GitHub OIDC provider and GitHub Actions role; when false, it uses an existing role."
  type        = bool
  default     = false
}

variable "state_bucket_name" {
  type = string
}

variable "github_actions_role_name" {
  description = "Name of the GitHub Actions IAM role."
  type        = string
}

variable "state_key_prefix" {
  description = "Object key prefix in the state bucket that Terraform can read/write (e.g. dev/*)."
  type        = string
  default     = "dev/*"
}

data "aws_iam_role" "github_actions_existing" {
  count = var.create_github_actions_role ? 0 : 1
  name  = var.github_actions_role_name
}

resource "aws_s3_bucket" "tf_state" {
  bucket = var.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_actions_role ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]
}

resource "aws_iam_role" "github_actions" {
  count = var.create_github_actions_role ? 1 : 0
  name  = var.github_actions_role_name

  lifecycle {
    precondition {
      condition     = length(trim(var.github_repo_owner, " ")) > 0 && length(trim(var.github_repo_name, " ")) > 0
      error_message = "When create_github_actions_role=true, github_repo_owner and github_repo_name must be provided."
    }
  }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow",
        Principal = {
          Federated = aws_iam_openid_connect_provider.github[0].arn
        },
        Action = "sts:AssumeRoleWithWebIdentity",
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          },
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo_owner}/${var.github_repo_name}:*"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_admin" {
  count      = var.create_github_actions_role ? 1 : 0
  role       = aws_iam_role.github_actions[0].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

locals {
  github_actions_role_name_effective = var.create_github_actions_role ? aws_iam_role.github_actions[0].name : data.aws_iam_role.github_actions_existing[0].name
  github_actions_role_arn_effective  = var.create_github_actions_role ? aws_iam_role.github_actions[0].arn : data.aws_iam_role.github_actions_existing[0].arn
}

resource "aws_iam_role_policy" "github_actions_backend_access" {
  name = "terraform-backend-access-${var.state_bucket_name}"
  role = local.github_actions_role_name_effective

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BucketList"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = "arn:aws:s3:::${var.state_bucket_name}"
      },
      {
        Sid    = "S3StateObjectReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "arn:aws:s3:::${var.state_bucket_name}/${var.state_key_prefix}"
      },
    ]
  })
}

output "state_bucket_name" {
  value = aws_s3_bucket.tf_state.bucket
}

output "github_actions_backend_policy_name" {
  description = "Inline policy name that grants backend state/lock access to GitHub Actions."
  value       = aws_iam_role_policy.github_actions_backend_access.name
}

output "github_actions_role_arn" {
  description = "The ARN of the IAM role for GitHub Actions to assume."
  value       = local.github_actions_role_arn_effective
}
