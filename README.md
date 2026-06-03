# Banking Agent

A conversational banking system that lets users move money, query transactions, buy airtime, and manage accounts — entirely through WhatsApp or Telegram, in English, Pidgin, Yoruba, Igbo, Hausa, or French.

Built solo over 6 months as a production-style AI and fintech systems project.

## What Makes This Different

This system is designed around the harder backend and orchestration cases that appear once conversational finance moves beyond simple single-account commands:

### Multi-Account Pooling

Users link multiple bank accounts via BVN verification and operate across them as a unified financial surface. The transfer engine supports **dual-account pooling** — splitting a single transfer across two source accounts with explicit or automatic allocation:

```
"send 100k to mum, 60k from access and 40k from gtb"
→ Decomposes into two coordinated debits with idempotent execution
```

This includes percentage-of-balance transfers (`"send half my zenith to mum"`), full-balance sweeps, and reference-based amounts (`"send the same as last time"`).

### Cross-Bank Account Linking

Users verify once via BVN and link all discovered bank accounts. The system deduplicates against already-linked accounts, supports relinking additional accounts later, and maintains account readiness state across all linked banks. Queries and transfers resolve against the full linked-account set, not a single wallet.

### Recipient Fanout

A single instruction can target multiple recipients with proportional or explicit splits:

```
"split 20k between mum and gaines 70/30"
→ recipient_allocations: [{mum: 14000}, {gaines: 6000}]
```

The planner decomposes this into independent execution tasks with per-recipient confirmation flows.

### Durable Transaction Mirror

Transaction queries don't hit the banking provider on every request. The system maintains a **coverage-aware local mirror** in Postgres — syncing transaction windows from providers, tracking coverage gaps, and serving queries from the mirror with automatic fallback. This makes query latency predictable and provider-independent.

### Typed Query Continuation

Follow-up questions like `"show details"`, `"how is that 50k?"`, or `"what about last month?"` are anchored by **typed surface state** — scope, filters, answer shape, and continuation acts — with narrow deterministic recovery only where the model output is weak. That keeps drill-down, pagination, and evidence retrieval stable across turns.

## Core Capabilities

### Query Intelligence
- Search transactions by recipient, amount, date range, bank, narration, or category
- Answer direct fact questions: `"when last did I send mum money?"`
- Grouped summaries, totals, breakdowns, and cross-account comparisons
- Conversational follow-ups with typed continuation acts and narrow deterministic recovery
- Multilingual: English, Nigerian Pidgin, Yoruba, Igbo, Hausa, French, and mixed phrasing

### Financial Workflows
- Transfer orchestration with confirmation, task decomposition, and idempotent execution
- Dual-account pooling and recipient fanout
- Account-aware transfers: percentages, full-balance, and reference-based amounts
- Beneficiary management: save, list, delete, with fuzzy alias matching and post-transfer suggestion
- Airtime and data purchase flows with source account selection
- Automatic receipt generation and outbound notifications

### Account Management
- BVN-based onboarding with OTP verification
- Multi-bank account linking and relinking
- Account readiness validation before financial operations
- Balance queries across all linked accounts

## Architecture

```text
User (WhatsApp / Telegram)
  → Gateway ingress (webhook validation, message routing)
  → Core chat worker (orchestrator graph + domain worker dispatch)
  → Domain workers (query, transfer, airtime, data, beneficiary, account, support, FAQ)
  → Financial execution (idempotent workers, provider abstraction)
  → Receipt generation + notification delivery
```

### Runtime Shape

| Service | Responsibility |
|---------|---------------|
| `apps/gateway/` | Ingress endpoints, webhook routing, channel adapters (WhatsApp, Telegram) |
| `apps/chat/` | Orchestration, task planning, conversational runtime, and domain worker dispatch |
| `apps/transaction/` | Transaction worker runtime, async financial worker entrypoints |
| `apps/receipt/` | Receipt rendering and async worker entrypoints |
| `banking/` | Product core: account, account onboarding, FAQ, support, beneficiary, airtime, data, transfer, transaction query, transaction-flow helpers, policy, presentation, receipts, persistence, runtime, repositories |
| `shared/` | Infrastructure primitives: config, database models, provider clients, queues, cache, messaging contracts, utilities |

### Orchestrator / Worker Contract

The chat runtime deliberately separates domain readiness from conversation control.

Domain workers are stateless task evaluators. Transfer, airtime, data, account, beneficiary, support, query, and FAQ workers inspect the current task payload plus loaded context, then return a typed result. Transaction workers can request:

- `NEEDS_INPUT` when task-specific details are missing or ambiguous.
- `NEEDS_CONFIRMATION` when a complete money-moving task needs user confirmation.
- `NEEDS_AUTH` when a confirmed task needs PIN/authorization.
- `OK` or `FAILED` when the worker can complete or reject the task pass.

Workers do not own durable conversation interrupts. They supply domain artifacts such as required fields, prompts, confirmation snapshots, confirmation summaries, patches, and receipts. The orchestrator owns persistence and user-turn control: it applies worker patches, updates task stages, creates `PendingInterrupt`, emits outbox/actionable messages, pauses execution, and resumes the graph after user input.

The orchestrator also arbitrates blockers at wave level. When multiple tasks run in one wave, it persists only one active interrupt using this priority:

```text
missing input > confirmation > auth
```

This means a sibling task that still needs input intentionally defers a ready auth request. Confirmation and auth requests from multiple same-wave tasks are intentionally collapsed into one batch confirmation or one batch auth prompt, with the affected task IDs and per-task snapshots preserved. This is not the worker deciding less; it is the orchestrator presenting one safe conversational checkpoint for the whole wave.

The invariant is:

```text
Workers request blockers. The orchestrator arbitrates blockers and persists exactly one PendingInterrupt.
```

### Provider Abstraction

The system is not locked to any single banking provider. All account data, transactions, and payment execution go through typed abstract interfaces (`BankDataProvider`, `PaymentProvider`, `BillProvider`). Current implementations include Mono (open banking) and Flutterwave (payments/bills), with a mock provider for development and testing.

## Why The Implementation Is Interesting

The main engineering work is around making LLM-driven financial flows behave predictably under real product pressure:

- **Typed internal contracts**: Parser output is normalized into typed Pydantic models before any business logic executes. The system never passes raw LLM output to financial operations.
- **Planner/executor separation**: A task planner decomposes natural language into typed execution tasks. Domain workers execute against contracts, not text.
- **Deterministic bypasses**: Common query patterns (balance checks, account lookups) are classified and routed deterministically, skipping the LLM entirely for latency-critical paths.
- **Guardrail layers**: The orchestrator applies spurious affirmation filtering, beneficiary routing contracts, and mandate de-escalation before dispatching tasks.
- **Failure-driven hardening**: The test suite is built primarily from observed production failures converted into regression tests — especially around multilingual parsing, query continuation, transfer payload normalization, and recipient resolution.

### Recent Work
- Moved account, account onboarding, FAQ, support, beneficiary, airtime, data, transaction query, and shared transaction-flow helper ownership into `banking/` product modules with no app-side compatibility aliases
- Decomposed the query core into compiler, continuation, grounding, and presentation subsystems
- Coverage-aware bank transaction mirror for durable local query reads
- Locale-aware beneficiary summary parsing
- Deterministic fast-path classification in the gate node and extraction node
- Hardened transfer payloads: recipient fanout binding, percentage/balance resolution, dual-account coordination

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+; checked against Python 3.12 |
| API | FastAPI + Uvicorn |
| Orchestration | LangGraph / LangChain |
| Database | PostgreSQL 16 |
| Cache/Transport | Redis 7 |
| Infra | Docker Compose, AWS (S3, SES, RDS) |
| Quality | Ruff, MyPy, Pytest (1,000+ tests) |

## Quality and Verification

- Typed Pydantic contracts across all execution layers
- `ruff` for linting, `mypy` for static type checking
- 1,000+ regression tests across query, transfer, orchestrator, formatting, and provider behavior
- Failure-driven test development: observed breakages are systematically converted into assertions
- 1,300+ commits and ~105K lines of production Python in the current codebase

## Repository Layout

```text
banking_agent/
├── apps/
│   ├── gateway/         # ingress, webhooks, channel adapters
│   ├── chat/            # orchestration, remaining app-owned workers, task planning
│   ├── transaction/     # transaction worker runtime app
│   └── receipt/         # receipt rendering and worker entrypoints
├── banking/             # banking product core, policy, presentation, runtime, repositories
├── shared/              # infrastructure primitives, clients, config, queue/cache, database models
├── tests/               # 1,000+ regression and integration tests
├── data/                # FAQ and runtime seed content
├── alembic/             # database migrations
├── infrastructure/      # AWS / deployment configuration
├── docker-compose.yml   # canonical runtime stack for VPS and local parity
├── deploy-stack.sh      # canonical stack entrypoint for local + VPS runtime shape
└── pyproject.toml
```

## Local Development

```bash
bash scripts/setup.sh
make local-stack-migrate
```

Create `.env` from your local secrets/template before starting the stack. For local testing with your existing Postgres and Redis containers, set `DATABASE_URL`, `REDIS_URL`, and `ASYNC_TRANSPORT=redis`, then run `make local-stack-migrate`. Later runs can use `make local-stack`.

For the production-shaped stack locally, use the same entrypoint as VPS:

```bash
IMAGE_TAG=latest ./deploy-stack.sh local
```

If you already built/tagged the VPS images locally, skip registry pulls:

```bash
SKIP_PULL=1 IMAGE_TAG=local ./deploy-stack.sh local
```

| Endpoint | URL |
|----------|-----|
| Gateway (direct) | `http://localhost:8000` |
| Transaction worker health | `http://localhost:8003/health` |
| Receipt worker health | `http://localhost:8002/health` |
| Caddy Front Door | `http://localhost` |

## Runtime Operations

### Service Ownership

The supported deployment shape is VPS-only. Service presence is the ownership switch: stop a service to disable that runtime role.

| Service | Owns |
|---------|------|
| `gateway` | Webhook ingress and inbound work publishing |
| `chat-worker` | Chat-critical queues: `message.received`, `flow_event.process` |
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
- `ASYNC_TRANSPORT`
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

1. Check Redis stream or SQS depth and stale-claim operational events.
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
- host AWS credentials mounted only when selected transports/providers still require AWS

The GitHub VPS deploy workflow builds and pushes runtime images, invokes `./deploy-stack.sh remote`, and verifies `/health`, `/transaction/health`, and `/receipt/health`. Required secrets are `VPS_SSH_PRIVATE_KEY`, `GHCR_PULL_USERNAME`, and `GHCR_PULL_TOKEN`. Optional variables are `VPS_HOST`, `VPS_SSH_USER`, `VPS_APP_DIR`, and `VPS_SSH_PORT`.

Rollback: stop the affected VPS service, re-enable the same ownership on the old runtime only if still available, then confirm only one side is consuming queues or sending replies.

### AWS Backend Bootstrap

`infrastructure/aws-backend-bootstrap` owns Terraform backend bootstrap resources and GitHub Actions backend access. CI defaults use `us-east-1`; Terraform locking uses S3 native lockfiles.

Fast backend-permission repair:

```bash
cd infrastructure/aws-backend-bootstrap
terraform init
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

If migrating remote state from `eu-west-1`, copy the existing state object into the `us-east-1` bucket, update the backend block, run `terraform init -migrate-state`, and delete old backend resources only after plan/apply succeeds.

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
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario quick --phone 2348162511023 --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario mvp --phone 2348162511023 --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario query --phone 2348162511023 --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario faq --phone 2348162511023 --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario query-deep --phone 2348162511023 --channel telegram --seed --reset-session
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

## Code Navigation

Start from public entrypoints and import concrete modules directly. Account, account onboarding, FAQ, support, transaction query, beneficiary, airtime, data, transfer, and shared transaction-flow helpers live only under `banking/`.

| Area | Start Here | Notes |
|------|------------|-------|
| Orchestrator | `apps/chat/src/agent/orchestrator/agent.py` | Runtime orchestration, media preprocessing, context persistence, graph handler setup |
| Graph workflows | `apps/chat/src/agent/orchestrator/workflows/` | Organized by phase: lifecycle, gate, interrupt, planner, execution |
| Gate workflow | `apps/chat/src/agent/orchestrator/workflows/gate/node.py` | Ordered pre-planner fast paths and semantic routing; stage order in `registry.py` is behavior |
| Task handlers | `apps/chat/src/agent/orchestrator/task_handlers/runtime.py` | Post-planner task-family routing and aggregation |
| Domain workers | `banking/accounts/`, `banking/faq/`, `banking/support/`, `banking/transactions/query/`, `banking/transactions/shared/`, `banking/beneficiaries/`, `banking/bills/airtime/`, `banking/bills/data/`, `banking/transfers/` | Product-owned domains and shared transaction-flow helpers live under `banking/`; chat owns orchestration and dispatch |
| Query worker | `banking/transactions/query/worker.py` | Parser/compiler, continuations, grounding, fetching, answer handlers, presentation |
| Account worker | `banking/accounts/management/worker.py` | Account list/count/balance, link-account flow start, default-account changes, unlink handling |
| Transfer worker | `banking/transfers/worker.py` | Extraction, resolution, validation, funding, confirmation, payout preparation, execution |
| Airtime worker | `banking/bills/airtime/worker.py` | Extraction, resolution, validation, source selection, confirmation, scheduling, execution |
| Data worker | `banking/bills/data/worker.py` | Extraction, plan selection/query, source selection, validation, confirmation, scheduling, execution |
| Support worker | `banking/support/worker.py` | Policy check, reference follow-up, classification, resolver, handler dispatch |
| FAQ worker | `banking/faq/worker.py` | FAQ intent guard, retrieval, synthesis, final answer safety |
| Beneficiary worker | `banking/beneficiaries/worker.py` | Beneficiary list/delete management; save-from-suggestion remains in the suggestion service |
| Presentation copy | `banking/presentation/formatters/` | Deterministic user-facing copy shared by workers and webhooks |

## Positioning

Built solo over 6 months as proof of deep capability in:

- **AI agent orchestration** — multi-step planning, typed execution, guardrails, and deterministic bypasses
- **Fintech systems engineering** — idempotent transactions, provider abstraction, multi-account coordination
- **Stateful conversational products** — typed continuation, cross-turn context, multilingual handling
- **Backend architecture** — async service mesh, queue-backed workers, coverage-aware data mirroring

If you are evaluating this repository for hiring or technical partnership, the strongest areas to inspect are:

| Area | Path |
|------|------|
| Query orchestration | `banking/transactions/query/` |
| Account management | `banking/accounts/management/` |
| Beneficiary management | `banking/beneficiaries/` |
| Transfer engine | `banking/transfers/` |
| Task planner + guardrails | `apps/chat/src/agent/orchestrator/` |
| Account linking | `banking/accounts/onboarding/` |
| Query regression tests | `tests/query/` |
| Orchestrator tests | `tests/orchestrator/` |
