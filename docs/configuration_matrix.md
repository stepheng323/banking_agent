# Configuration Matrix

## Source of Truth

- Canonical SSM path prefix: `/banking-agent/<env>/KEY_NAME`
- Managed by Terraform module: `infrastructure/aws/modules/config-ssm`
- Secret keys are stored as `SecureString`
- Non-secret runtime config keys are stored as `String`

## Shared Runtime Baseline (all services)

All runtimes receive these baseline keys:

- `APP_ENV`
- `ENVIRONMENT`
- `PROJECT_NAME`
- `AWS_REGION`
- `AWS_ACCOUNT_ID`
- `DATABASE_URL`
- `REDIS_URL`
- `APP_DOMAIN`
- `ACME_EMAIL`
- `MONO_USE_MOCK`
- `RUNTIME_STACK_ROLE`
- `CHAT_TRANSPORT`
- `ASYNC_TRANSPORT`
- `ENABLE_WEBHOOK_INGRESS`
- `ENABLE_CHAT_CONSUMERS`
- `ENABLE_TRANSACTION_WORKER`
- `ENABLE_FUNDING_WORKER`
- `ENABLE_PAYOUT_WORKER`
- `ENABLE_REFUND_WORKER`
- `ENABLE_RECEIPT_WORKER`
- `ENABLE_OUTBOUND_SENDER`

## core-chat-worker (ECS)

Required keys:

- `OPENAI_API_KEY`
- `PLANNER_MODEL`
- `INTERRUPT_ROUTER_MODEL`
- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `META_ACCESS_TOKEN`
- `META_VERIFY_TOKEN`
- `META_PHONE_NUMBER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_MINI_APP_BASE_URL`
- `S3_BUCKET_NAME`
- `ONBOARDING_FLOW_ID`
- `ACCOUNT_LINKING_FLOW_ID`
- `PIN_CONFIRMATION_FLOW_ID`
- `WHATSAPP_FLOW_PRIVATE_KEY`
- `DEFAULT_CHANNEL`
- `SOUL_POLICY_PATH`
- `ENABLE_CHANNEL_OPTION_UX_V2`
- `TTL_SECONDS`
- `FLOW_SESSION_TIMEOUT`
- `PENDING_TRANSACTION_TTL`

Notes:

- `INTERRUPT_ROUTER_MODEL` is optional; when missing, runtime defaults to `PLANNER_MODEL`.
- Setting a dedicated `INTERRUPT_ROUTER_MODEL` is still recommended for latency and routing isolation.

Delivery model:

- Non-secrets from SSM `String` into ECS `environment`
- Secrets from SSM `SecureString` into ECS `secrets`

## transaction-worker (Lambda)

Required keys:

- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`
- Shared runtime baseline keys

Delivery model:

- Deploy-time injection from SSM (`data.aws_ssm_parameter`)

## receipt-worker (Lambda)

Required keys:

- `META_ACCESS_TOKEN`
- `META_VERIFY_TOKEN`
- `META_PHONE_NUMBER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_MINI_APP_BASE_URL`
- `WHATSAPP_FLOW_PRIVATE_KEY`
- `S3_BUCKET_NAME`
- Shared runtime baseline keys

Delivery model:

- Deploy-time injection from SSM (`data.aws_ssm_parameter`)

## gateway-lambda (Lambda)

Required keys:

- `META_ACCESS_TOKEN`
- `META_VERIFY_TOKEN`
- `META_PHONE_NUMBER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_MINI_APP_BASE_URL`
- `ONBOARDING_FLOW_ID`
- `ACCOUNT_LINKING_FLOW_ID`
- `PIN_CONFIRMATION_FLOW_ID`
- `WHATSAPP_FLOW_PRIVATE_KEY`
- `DEFAULT_CHANNEL`
- Shared runtime baseline keys

Delivery model:

- Deploy-time injection from SSM (`data.aws_ssm_parameter`)

## IAM Guardrails

- ECS execution/task roles: `ssm:GetParameter`, `ssm:GetParameters`, `kms:Decrypt`
- Lambda worker roles: `ssm:GetParameter`, `ssm:GetParameters`, `kms:Decrypt`
- Scope all SSM permissions to Terraform-created parameter ARNs only

## Operational Notes

- Terraform state must stay encrypted and access-restricted because Lambda env values are materialized at deploy time.
- Missing/empty critical keys should fail startup outside `dev`.
