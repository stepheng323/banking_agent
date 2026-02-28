# AWS Backend Bootstrap Runbook

This stack now owns:
- Terraform backend resources (S3 + DynamoDB lock table)
- GitHub OIDC provider and GitHub Actions IAM role
- Backend access inline policy for the GitHub Actions role

## Prerequisites

- AWS credentials with permissions to manage S3, DynamoDB, and IAM role resources.
- Target role name: `banking-agent-github-actions-role-dev`.

## 1) Detach GitHub OIDC Resources from `infrastructure/aws` State

Run this once to avoid destroy actions after removing `module.github_oidc` from `infrastructure/aws`:

```bash
cd infrastructure/aws
terraform init -input=false
terraform state rm module.github_oidc.aws_iam_role_policy_attachment.github_actions_admin
terraform state rm module.github_oidc.aws_iam_role.github_actions
terraform state rm module.github_oidc.aws_iam_openid_connect_provider.github
```

## 2) Initialize Bootstrap Stack

```bash
cd ../aws-backend-bootstrap
terraform init
```

## 3) Import Existing Resources (if not already in bootstrap state)

Use these imports when resources already exist in AWS but are not in this stack state.

```bash
terraform import aws_s3_bucket.tf_state banking-agent-tf-state-dev
terraform import aws_s3_bucket_versioning.tf_state banking-agent-tf-state-dev
terraform import aws_s3_bucket_server_side_encryption_configuration.tf_state banking-agent-tf-state-dev
terraform import aws_s3_bucket_public_access_block.tf_state banking-agent-tf-state-dev
terraform import aws_dynamodb_table.tf_lock banking-agent-tf-locks-dev

terraform import aws_iam_openid_connect_provider.github arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com
terraform import aws_iam_role.github_actions banking-agent-github-actions-role-dev
terraform import aws_iam_role_policy_attachment.github_actions_admin banking-agent-github-actions-role-dev/arn:aws:iam::aws:policy/AdministratorAccess
```

Optional: if this inline backend policy already exists but is not in state:

```bash
terraform import aws_iam_role_policy.github_actions_backend_access banking-agent-github-actions-role-dev:terraform-backend-access-banking-agent-tf-state-dev
```

## 4) Plan

```bash
terraform plan \
  -var='aws_region=eu-west-1' \
  -var='state_bucket_name=banking-agent-tf-state-dev' \
  -var='lock_table_name=banking-agent-tf-locks-dev' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='state_key_prefix=dev/*'
```

## 5) Apply

```bash
terraform apply \
  -var='aws_region=eu-west-1' \
  -var='state_bucket_name=banking-agent-tf-state-dev' \
  -var='lock_table_name=banking-agent-tf-locks-dev' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='state_key_prefix=dev/*'
```

## 6) Verify

- Re-run `.github/workflows/deploy-dev.yml`.
- Confirm `terraform init` succeeds in the `terraform-plan` job.
