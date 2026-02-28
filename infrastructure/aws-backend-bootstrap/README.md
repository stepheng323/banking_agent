# AWS Backend Bootstrap Runbook

Use this stack to bootstrap Terraform backend resources and grant GitHub Actions backend access.
Current CI defaults use `us-east-1`.
Terraform state locking uses **S3 native lockfiles** (`use_lockfile = true`), so a DynamoDB lock table is no longer required.

## Automated Bootstrap Workflow

Use [bootstrap-aws-dev.yml](/home/abiodun/dev/personal/banking_agent/.github/workflows/bootstrap-aws-dev.yml) for ongoing plan/apply automation of this stack.

Required repository secret:
- `AWS_BOOTSTRAP_ROLE_ARN_DEV`: ARN of an admin-capable IAM role trusted for GitHub OIDC.

Usage:
- On `push` to `main` (for bootstrap path changes), the workflow runs `plan` then auto-runs `apply`.
- You can still run `workflow_dispatch` with `action=plan` or `action=apply`.
- Keep `create_github_actions_role=false` for backend-access-only updates.
- Set `create_github_actions_role=true` only when you have completed role/OIDC state migration into this stack.

## Fast Fix for Current 403 (Recommended)

This path keeps your existing role and only adds the missing backend permissions.

### 1) Initialize

```bash
cd infrastructure/aws-backend-bootstrap
terraform init
```

### 2) Import backend resources (only if not already in this stack state)

```bash
terraform import aws_s3_bucket.tf_state banking-agent-tf-state-dev-use1-808537413474
terraform import aws_s3_bucket_versioning.tf_state banking-agent-tf-state-dev-use1-808537413474
terraform import aws_s3_bucket_server_side_encryption_configuration.tf_state banking-agent-tf-state-dev-use1-808537413474
terraform import aws_s3_bucket_public_access_block.tf_state banking-agent-tf-state-dev-use1-808537413474
```

### 3) Plan/apply backend access policy to existing role

`create_github_actions_role=false` is the default and tells Terraform to use the existing role.

```bash
terraform plan \
  -var='aws_region=us-east-1' \
  -var='state_bucket_name=banking-agent-tf-state-dev-use1-808537413474' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  -var='state_key_prefix=dev/*'

terraform apply \
  -var='aws_region=us-east-1' \
  -var='state_bucket_name=banking-agent-tf-state-dev-use1-808537413474' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  -var='state_key_prefix=dev/*'
```

### 4) Re-run CI

Re-run `.github/workflows/deploy-dev.yml`.  
`terraform init` in `terraform-plan` should no longer fail with `s3:ListBucket` 403.

## Backend Migration to us-east-1 (from existing eu-west-1 backend)

If your current remote state is still in `eu-west-1`, migrate state before deleting old backend resources:

```bash
aws s3 cp s3://banking-agent-tf-state-dev/dev/terraform.tfstate \
  s3://banking-agent-tf-state-dev-use1-808537413474/dev/terraform.tfstate \
  --source-region eu-west-1 \
  --region us-east-1
```

Then update your Terraform backend block (where your deploy stack is defined) to:
- `bucket = "banking-agent-tf-state-dev-use1-808537413474"`
- `use_lockfile = true`
- `region = "us-east-1"`

Run:

```bash
terraform init -migrate-state
```

Only delete `eu-west-1` backend resources after `plan`/`apply` succeeds on the new backend.

## Optional: Full Role Ownership Migration to Bootstrap

If you want this bootstrap stack to own OIDC + role going forward:

### A) Detach old OIDC resources from `infrastructure/aws` state

```bash
cd ../aws
terraform init -input=false
terraform state rm module.github_oidc.aws_iam_role_policy_attachment.github_actions_admin
terraform state rm module.github_oidc.aws_iam_role.github_actions
terraform state rm module.github_oidc.aws_iam_openid_connect_provider.github
```

### B) Import IAM resources into bootstrap and enable ownership

```bash
cd ../aws-backend-bootstrap
terraform import \
  -var='create_github_actions_role=true' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  aws_iam_openid_connect_provider.github[0] arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com

terraform import \
  -var='create_github_actions_role=true' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  aws_iam_role.github_actions[0] banking-agent-github-actions-role-dev

terraform import \
  -var='create_github_actions_role=true' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  aws_iam_role_policy_attachment.github_actions_admin[0] banking-agent-github-actions-role-dev/arn:aws:iam::aws:policy/AdministratorAccess
```

Then apply with role creation ownership enabled:

```bash
terraform apply \
  -var='aws_region=us-east-1' \
  -var='state_bucket_name=banking-agent-tf-state-dev-use1-808537413474' \
  -var='github_actions_role_name=banking-agent-github-actions-role-dev' \
  -var='github_repo_owner=stepheng323' \
  -var='github_repo_name=banking_agent' \
  -var='state_key_prefix=dev/*' \
  -var='create_github_actions_role=true'
```
