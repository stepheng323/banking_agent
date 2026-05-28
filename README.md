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
| `apps/chat/` | Orchestration, domain workers, task planning, shared financial logic |
| `apps/transaction/` | Transaction worker runtime, async financial worker entrypoints |
| `apps/receipt/` | Receipt rendering and async worker entrypoints |
| `shared/` | Contracts, provider clients, database models, i18n, runtime config |

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
│   ├── chat/            # orchestration, domain workers, task planning
│   ├── transaction/     # transaction worker runtime app
│   └── receipt/         # receipt rendering and worker entrypoints
├── shared/              # shared contracts, config, clients, repositories
├── tests/               # 1,000+ regression and integration tests
├── docs/                # runtime ownership, configuration, policy docs
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

See [docs/runtime_ownership.md](docs/runtime_ownership.md) and [docs/configuration_matrix.md](docs/configuration_matrix.md) for deployment configuration.

## Positioning

Built solo over 6 months as proof of deep capability in:

- **AI agent orchestration** — multi-step planning, typed execution, guardrails, and deterministic bypasses
- **Fintech systems engineering** — idempotent transactions, provider abstraction, multi-account coordination
- **Stateful conversational products** — typed continuation, cross-turn context, multilingual handling
- **Backend architecture** — async service mesh, queue-backed workers, coverage-aware data mirroring

If you are evaluating this repository for hiring or technical partnership, the strongest areas to inspect are:

| Area | Path |
|------|------|
| Query orchestration | `apps/chat/src/agent/workers/query/` |
| Transfer engine | `apps/chat/src/agent/workers/transfer/` |
| Task planner + guardrails | `apps/chat/src/agent/orchestrator/` |
| Account linking | `shared/services/onboarding/` |
| Query regression tests | `tests/query/` |
| Orchestrator tests | `tests/orchestrator/` |
