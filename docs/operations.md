# Operations Guide

Detailed runtime, configuration, observability, incident, deploy, policy, and smoke-test notes for the Nenya AI banking agent.

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Runbook | A repeatable operational procedure for deployment, debugging, recovery, or validation. |
| Incident | A production-impacting failure or degraded behavior that needs investigation and recovery. |
| Observability | Logs, traces, metrics, and events used to understand runtime behavior. |
| Env var | Environment variable that configures runtime behavior, credentials, or feature settings. |
| Deploy | Releasing a runtime version into the supported VPS stack. |
| Rollback | Returning to a prior known-good runtime version. |
| Live smoke | A limited post-deploy check against safe flows to verify the system is healthy. |

Example: after deploy, a live smoke should verify ingress, chat-worker processing, and notification delivery without using real-money operations unless the runbook explicitly requires a controlled provider test.

## Runtime Operations

### Service Ownership

The supported deployment shape is VPS-only. Service presence is the ownership switch: stop a service to disable that runtime role.

| Service | Owns |
|---------|------|
| `gateway` | Webhook ingress and inbound work publishing |
| `chat-worker` | Chat-critical queues: `message.received`, `flow_event.process`, and scheduled transaction dispatch |
| `transaction-worker` | Financial queues: `transaction.execute`, `funding.process`, `funding.reconcile`, `payout.process`, `payout.reconcile`, `refund.process`, `refund.reconcile` |
| `receipt-worker` | Outbound messaging and receipt queues: `notification.send`, `receipt.process` |

Do not run multiple stacks against the same logical queue ownership. Webhook ingress belongs only to `gateway`, chat queues only to `chat-worker`, financial queues only to `transaction-worker`, and receipt/outbound queues only to `receipt-worker`.

### Configuration

Runtime configuration comes from the `.env` used by `docker-compose.yml`.

Baseline infrastructure:

- `DATABASE_URL`
- `REDIS_URL`
- `FIELD_ENCRYPTION_KEY_B64` (base64 AES key, 16/24/32 bytes after decode)
- `FIELD_BLIND_INDEX_KEY_B64` (base64 HMAC key for equality lookup)
- `FIELD_ENCRYPTION_KEY_ID` (key identifier stored in ciphertext envelopes)
- `APP_DOMAIN`
- `ACME_EMAIL`
- `CHAT_TRANSPORT`
- optional DB pool controls: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`

Gateway integrations:

- `META_ACCESS_TOKEN`
- `META_VERIFY_TOKEN`
- `META_PHONE_NUMBER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_MINI_APP_BASE_URL`
- `FLUTTERWAVE_WEBHOOK_SECRET_HASH`
- `ONBOARDING_FLOW_ID`
- `ACCOUNT_LINKING_FLOW_ID`
- `PIN_CONFIRMATION_FLOW_ID`
- `WHATSAPP_FLOW_PRIVATE_KEY`
- `DEFAULT_CHANNEL`

Versioned WhatsApp flow specs live beside the flow handlers:

- `apps/gateway/api/webhooks/whatsapp/flows/specs/whatsapp_onboarding_flow.json`
- `apps/gateway/api/webhooks/whatsapp/flows/specs/whatsapp_pin_flow.json`

Chat worker integrations:

- `OPENAI_API_KEY`
- `PLANNER_MODEL`
- `INTERRUPT_ROUTER_MODEL` (optional, defaults to `PLANNER_MODEL`)
- `MEDIA_IMAGE_MODEL` (optional, defaults to `gpt-5-mini`)
- `AUDIO_TRANSCRIPTION_MODEL` (optional, defaults to `gpt-4o-mini-transcribe`)
- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `PAYOUT_RECONCILIATION_MIN_AGE_SECONDS`
- `PAYOUT_RECONCILIATION_BATCH_SIZE`
- `PAYOUT_RECONCILIATION_INTERVAL_SECONDS` (`0` disables the loop)
- `S3_BUCKET_NAME`
- `ASSISTANT_PROFILE_PATH`
- `CAPABILITY_POLICY_PATH`
- `DOMAIN_GUARDRAILS_PATH`
- `ENABLE_CHANNEL_OPTION_UX_V2`
- `TTL_SECONDS`
- `FLOW_SESSION_TIMEOUT`
- `PENDING_TRANSACTION_TTL`
- `CHAT_WORKER_MAX_CONCURRENCY`
- `CHAT_THREAD_LOCK_TTL_SECONDS`
- `CHAT_THREAD_LOCK_RENEW_SECONDS`
- `CHAT_THREAD_LOCK_WAIT_SECONDS`
- `CHAT_LATEST_INBOUND_TTL_SECONDS`
- `LANGSMITH_TRACING`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `LLM_OBSERVABILITY_ENABLED` (default `true`)
- `LLM_TRACE_PRIVACY_MODE` (default `masked`)
- `LLM_TRACE_SAMPLE_RATE`

Transaction worker integrations:

- `MONO_API_KEY`
- `FLUTTERWAVE_SECRET_KEY`
- `FLUTTERWAVE_USE_SANDBOX`
- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`

Receipt worker integrations:

- WhatsApp and Telegram credentials
- `S3_BUCKET_NAME`
- `CHAT_PENDING_INPUT_PROMPT_DEBOUNCE_SECONDS`

Missing critical keys should fail startup outside local development.

### Observability

Health and readiness are split deliberately:

- `/health` is shallow process liveness.
- `/ready` checks required dependencies. Gateway checks DB and Redis, transaction worker checks DB, Redis, and reconciliation loop health, and receipt worker checks Redis.

LangSmith tracing is automatic for LangChain/LangGraph calls when the standard LangSmith environment variables are set:

- `LANGSMITH_TRACING=true`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`

The app only attaches safe trace tags and metadata. It does not manually attach raw prompts, raw user messages, account numbers, mandate IDs, PINs, OTPs, provider references, flow tokens, or media payloads. Trace metadata uses tags such as runtime, environment, channel, path, LLM role, and task domain, plus hashed channel/message identifiers.

Operational events are emitted as redacted structured logs with event names such as:

- Money flow: `funding_reconciliation_tick_failed`, `payout_reconciliation_tick_failed`, `refund_reconciliation_tick_failed`, `transaction_debit_reconciliation_tick_failed`, `bill_reconciliation_tick_failed`, `direct_transfer_reconciliation_tick_failed`.
- Ledger: `ledger_reconciliation_finding_opened`, `ledger_reconciliation_finding_resolved`, `ledger_reconciliation_support_ticket_created`.
- Webhooks: `mono_webhook_invalid_secret`, `mono_webhook_duplicate_ignored`, `flutterwave_webhook_unauthorized`, `flutterwave_webhook_duplicate_ignored`, `flutterwave_webhook_processing_failed`.
- Queues: `transaction_worker_stream_record_failed`, `receipt_worker_stream_record_failed`, `redis_stream_stale_record_claimed`, `sqs_message_left_for_retry`.
- LLM: `llm_call_completed`, `llm_orchestrator_safe_fallback`, `llm_media_audio_failed`, `llm_media_image_failed`, `llm_embedding_failed`.

### Incident Runbooks

For all money-flow incidents, start with the user-facing `transactions.id`, `transactions.idempotency_key`, provider reference, and the current `/ready` response from `transaction-worker`.

Stuck Mono funding or transaction debit:

1. Inspect `funding_steps` or `transaction_debit_steps` for `pending` or `processing` rows older than the configured reconciliation age.
2. Confirm `funding.reconcile` or `transaction_debit.reconcile` loop health in `/ready`.
3. Check Mono status by provider reference, not by retrying a new debit.
4. Let reconciliation apply terminal status; only intervene manually if the ledger finding remains open after provider truth is known.

Failed Flutterwave payout after pooled funding:

1. Verify every funding step that reached `confirmed`.
2. Confirm the funded transfer is `refunding` and refund jobs exist or reconciliation is enabled.
3. Check `ledger_exposure:*` findings for non-zero liability.
4. The user-facing transaction should not be `successful` unless Flutterwave payout is terminal successful.

Pending refund:

1. Inspect funding or transaction debit refund metadata: provider reference, attempt count, last checked time, and status.
2. Confirm `refund.reconcile` or `transaction_debit.refund_reconcile` loop health.
3. If provider says refunded, reconciliation should post the refund ledger entry before marking `refunded` or `reversed`.
4. If provider says failed or ambiguous after max attempts, keep the support ticket open and do not mark the transaction successful.

Bill failure after debit:

1. Confirm the Mono debit step is `confirmed`.
2. Check Flutterwave bill status by `"{idempotency_key}-bill"`.
3. If bill failed terminally, transaction remains failed while refund is pending; it becomes `reversed` only after refund confirmation.
4. Confirm completion notification context exists in `transactions.service_metadata`.

Ledger mismatch:

1. Run or wait for `ledger.reconcile.postings` to backfill missing deterministic ledger entries.
2. Run or wait for `ledger.reconcile.exposure` to calculate liability exposure.
3. Critical open findings must create or reuse one support ticket with intent `ledger_reconciliation`.
4. Never mutate ledger entries or lines; corrections are new reversal or adjustment entries.

Webhook replay or spoofing:

1. Confirm the provider signature/hash outcome in webhook operational events.
2. Check `processed_webhook_events` for the provider event ID.
3. Duplicate valid webhooks should be acknowledged and ignored; invalid webhooks should be rejected.
4. Do not replay raw webhook bodies into production unless the event ID and provider reference are understood.

Queue backlog:

1. Check Redis stream depth and stale-claim operational events.
2. Confirm only the owning runtime is consuming each queue family.
3. Restart the affected worker only after checking `/ready`; DB rows are the durable workflow source.

Provider outage:

1. Expect provider timeouts and reconciliation failures to emit operational events.
2. Keep new provider calls bounded by retry limits; avoid manual retry storms.
3. User-facing messages should remain processing/pending until provider truth is recovered.

High LLM fallback rate or latency spike:

1. Check `llm_orchestrator_safe_fallback`, `llm_call_completed`, and slow-call operational events.
2. Use LangSmith tags by runtime, role, channel, path, and task domain to isolate the failing role.
3. Prefer deterministic fast paths and guardrail fallbacks for money-moving conversations while investigating.

Tracing outage:

1. LangSmith outage must not block chat or financial execution.
2. Confirm `LLM_OBSERVABILITY_ENABLED` and LangSmith env vars.
3. Continue using redacted operational logs for runtime diagnosis.

### VPS Deploy And Rollback

`./deploy-stack.sh remote` is the canonical VPS bring-up path. It syncs stack assets, pulls pinned runtime images, and restarts the stack using managed `DATABASE_URL` and `REDIS_URL`; the compose file does not start local Postgres or Redis on the VPS.

Required host setup:

- Docker with Compose plugin
- `.env` on the VPS
- DNS for `APP_DOMAIN` pointing at the VPS public IP
- ports `80` and `443` open for ACME and HTTPS

The GitHub VPS deploy workflow builds and pushes runtime images, invokes `./deploy-stack.sh remote`, and verifies `/health`, `/transaction/health`, and `/receipt/health`. Required secrets are `VPS_SSH_PRIVATE_KEY`, `GHCR_PULL_USERNAME`, and `GHCR_PULL_TOKEN`. Optional variables are `VPS_HOST`, `VPS_SSH_USER`, `VPS_APP_DIR`, and `VPS_SSH_PORT`.

Rollback: stop the affected VPS service, re-enable the same ownership on the old runtime only if still available, then confirm only one side is consuming queues or sending replies.

## Runtime Policy And Brand

Assistant identity, capability support, and deterministic guardrails are split across runtime JSON files:

- `apps/chat/src/agent/assistant_profile/defaults/assistant_profile.json`: assistant name, description, positioning, tone, response rules, and safety rules.
- `banking/policy/defaults/capability_policy.json`: executable support matrix by domain and action.
- `banking/policy/guardrails/defaults/domain_guardrails.json`: deterministic thresholds and limits such as transfer name matching, dynamic-risk thresholds, query limits, and support thresholds.

Runtime env overrides:

- `ASSISTANT_PROFILE_PATH`
- `CAPABILITY_POLICY_PATH`
- `DOMAIN_GUARDRAILS_PATH`

The assistant is a calm, high-competence financial concierge for execution and money understanding. It is not a financial advisor. It should be crisp, context-aware, honest about unsupported features, and should offer supported alternatives when declining a request.

Supported domains include transfers, airtime, data, balances, account linking, transaction queries, receipts, support tickets, and FAQ answers. Unsupported capabilities include financial advice, investments, international transfers, all-time history, PDF exports, and CSV exports unless the policy is updated to support them.

Validate profile, policy, guardrails, and catalog completeness with:

```bash
uv run python -c "from apps.chat.src.agent.assistant_profile.loader import load_assistant_profile; from banking.policy.loader import load_policy; from banking.policy.validation import validate_policy_coverage; from banking.policy.guardrails.loader import load_guardrails; profile = load_assistant_profile('apps/chat/src/agent/assistant_profile/defaults/assistant_profile.json'); policy = load_policy('banking/policy/defaults/capability_policy.json'); guardrails = load_guardrails('banking/policy/guardrails/defaults/domain_guardrails.json'); validate_policy_coverage(policy); print('assistant profile ok:', profile.version); print('capability policy ok:', policy.version); print('guardrails ok:', guardrails.version)"
uv run pytest tests/shared/test_runtime_profile_policy.py
```

Brand source of truth:

- `APP_NAME`
- `APP_NAME_SHORT`
- `APP_NAME_ALIASES`
- `APP_LEGACY_NAMES`

Hardcoded brand names should live only as fallback defaults in `shared/config/settings.py`. External surfaces such as BotFather, WhatsApp Business profile, admin UI, frontend UI, and transcript labels must be renamed outside the chat runtime.

## Live Smoke Checklist

Use a seeded test user and do not use real-money accounts. Keep batch transactions to 5 tasks or fewer and pooled funding to 2 source accounts or fewer. Stale confirmations must not execute after `PENDING_TRANSACTION_TTL`; every money movement must re-render confirmation and require the existing PIN/authorization path.

Automated dry-runs:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario quick --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario mvp --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario query --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario faq --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario query-deep --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
```

Manual smoke areas:

- Greeting, capabilities, and casual non-banking redirect.
- FAQ answer, FAQ uncertainty, support transaction lookup, support ticket lookup.
- Beneficiary list, beneficiary follow-ups, ambiguous beneficiary selection.
- Account list, bank-specific account follow-ups, pending mandate explanation.
- Transaction query pagination, detail drill-down, fact follow-ups, and fresh-task exit from query memory.
- Single transfer confirmation edits, PIN authorization, processing response, and receipt summary.
- Airtime and data self-purchase, source account selection, confirmation, and execution.
- Mixed batch edit/remove/restore flows with one final batch summary.
- Pooled funding suggestion, explicit two-source split, and rejection of three-source pooling.
- Replay from completed and partial-failure summaries with new idempotency keys.
- Expired transaction sessions clearing safely before `Yes`, greeting, or fresh task input.

Failure reports should include channel, phone, timestamp, transcript, expected behavior, actual behavior, and screenshots or log excerpts.
