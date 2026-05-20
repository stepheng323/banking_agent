# Configuration Matrix

## Source of Truth

For the supported deployment model, runtime configuration comes from the `.env` used by [docker-compose.yml](/home/abiodun/dev/personal/banking_agent/docker-compose.yml).

Required baseline infrastructure settings:

- `DATABASE_URL`
- `REDIS_URL`
- `APP_DOMAIN`
- `ACME_EMAIL`

Common runtime ownership flags:

- `CHAT_TRANSPORT`
- `ASYNC_TRANSPORT`
- `DB_POOL_SIZE` (optional, defaults to `20`)
- `DB_MAX_OVERFLOW` (optional, defaults to `10`)
- `DB_POOL_TIMEOUT` (optional, defaults to `30`)

## gateway

Required app integrations:

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

## chat-worker

Required app integrations:

- `OPENAI_API_KEY`
- `PLANNER_MODEL`
- `INTERRUPT_ROUTER_MODEL` (optional, defaults to `PLANNER_MODEL`)
- `MEDIA_IMAGE_MODEL` (optional, defaults to `gpt-5-mini`)
- `AUDIO_TRANSCRIPTION_MODEL` (optional, defaults to `gpt-4o-mini-transcribe`)
- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `S3_BUCKET_NAME`
- `ASSISTANT_PROFILE_PATH`
- `CAPABILITY_POLICY_PATH`
- `DOMAIN_GUARDRAILS_PATH`
- `ENABLE_CHANNEL_OPTION_UX_V2`
- `TTL_SECONDS`
- `FLOW_SESSION_TIMEOUT`
- `PENDING_TRANSACTION_TTL`
- `CHAT_WORKER_MAX_CONCURRENCY` (optional, defaults to `8`)
- `CHAT_THREAD_LOCK_TTL_SECONDS` (optional, defaults to `120`)
- `CHAT_THREAD_LOCK_RENEW_SECONDS` (optional, defaults to `30`)
- `CHAT_THREAD_LOCK_WAIT_SECONDS` (optional, defaults to `60`)
- `CHAT_LATEST_INBOUND_TTL_SECONDS` (optional, defaults to `300`)

## transaction-worker

Required integrations:

- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`

## receipt-worker

Required integrations:

- `META_ACCESS_TOKEN`
- `META_VERIFY_TOKEN`
- `META_PHONE_NUMBER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_MINI_APP_BASE_URL`
- `WHATSAPP_FLOW_PRIVATE_KEY`
- `S3_BUCKET_NAME`
- `CHAT_PENDING_INPUT_PROMPT_DEBOUNCE_SECONDS` (optional, defaults to `1.5`)

## Host Requirements

- Docker with Compose plugin
- a valid `.env`
- host AWS credential directory when your chosen providers or transports still depend on AWS:
  - default VPS mount: `/root/.aws`
  - local override: `AWS_HOST_DIR=$HOME/.aws`

## Operational Notes

- Missing critical keys should fail startup outside `dev`.
- The supported container deployment path is the VPS image stack only.
- Service presence is the ownership switch. Stop a service to disable that runtime role.
- Local runs that need to mirror production should use:

```bash
./deploy-stack.sh local
```
