# Local Development

This guide covers local setup, common commands, local stack shape, safe testing, readiness scripts, and quality checks.

Use seeded users and mock or sandbox providers. Do not use real-money accounts for local or smoke-test flows.

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Local stack | The local set of services, database, Redis, and supporting processes that mimic deployment shape. |
| Migration | A database schema change applied before the app expects the new tables/columns to exist. |
| Seeded data | Test users, accounts, transactions, or provider fixtures safe for local/smoke flows. |
| Readiness check | A script or health check that confirms services and dependencies are usable. |
| Smoke test | A small end-to-end check that proves the main path works without exhaustive coverage. |
| Mock/sandbox provider | A non-real-money provider mode used for safe local testing. |

Example: before testing a transfer locally, run migrations, use seeded or sandbox accounts, then run a small smoke flow instead of connecting real-money accounts.

## Setup

Preferred setup path:

```bash
make setup
```

This uses `uv sync --all-extras --group dev`. The older `scripts/setup.sh` virtualenv path still exists, but `make setup` is the normal project entrypoint.

Create `.env` from local secrets before starting services. At minimum, local runs need database and Redis connectivity:

```bash
DATABASE_URL=...
REDIS_URL=...
```

Provider credentials depend on which flows you are testing. Mock/sandbox providers should be preferred locally.

## Local Stack

Run migrations and start the local stack:

```bash
make local-stack-migrate
```

After migrations are already applied:

```bash
make local-stack
```

Production-shaped local run through the deploy stack wrapper:

```bash
IMAGE_TAG=latest ./deploy-stack.sh local
```

If images are already built/tagged locally:

```bash
SKIP_PULL=1 IMAGE_TAG=local ./deploy-stack.sh local
```

Local endpoints:

| Endpoint | URL |
|---|---|
| Gateway | `http://localhost:8000` |
| Transaction worker health | `http://localhost:8003/health` |
| Receipt worker health | `http://localhost:8002/health` |
| Caddy front door | `http://localhost` |

## Individual Services

Run services separately when debugging one runtime:

```bash
make run-gateway
make run-chat-worker
make run-transaction-worker
make run-receipt
```

Do not run multiple stacks against the same logical queue ownership. Keep one owner for gateway ingress, chat queues, financial queues, and receipt/outbound queues.

## Quality Commands

```bash
make lint
make format-check
make test
```

`make type-check` is a convenience target scoped by the Makefile and currently does not behave like strict repo-wide enforcement. For focused checks, run:

```bash
uv run mypy <paths>
uv run mypy .
```

Use `make test-file FILE=path/to/test.py` for focused pytest runs.

## Readiness Scripts

Readiness scenarios live under `scripts/readiness*.py`.

Example dry runs:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario quick --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario mvp --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario query --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario variance-insight --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario planner --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session
```

Use explicit report paths when comparing latency or planner quality:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.readiness --mode dry-run --scenario planner --phone <TEST_PHONE_E164> --channel telegram --seed --reset-session --json-output logs/readiness/planner-local.json --transcript-output logs/readiness/planner-local.txt
```

## Database And Migrations

Common commands:

```bash
make db-migrate
make db-upgrade
make db-rollback
```

`make db-reset` is destructive and should only be used against disposable local data.

### Variance-insight demo data

The variance acceptance fixture is local/demo-only. It seeds a complete current-versus-previous-month bank-feed window, then rebuilds the canonical query transactions, semantic projections, and economic events. It includes operating income and spending, internal movement, investing/financing movement, an unchanged category, and an unresolved narration.

After the semantic-enrichment migration has been applied, reset and rebuild one disposable user explicitly:

```bash
make db-upgrade
PYTHONPATH=. uv run python -m scripts.seed_user_test_data --phone <TEST_PHONE_E164> --reset-query-data --yes
```

The command only prints aggregate semantic counts and masked account suffixes. `--reset-query-data` deletes that user's query-side demo sources, projections, coverage windows, and economic events before rebuilding them; do not use it for production users.

Acceptance transcript:

```text
Why did my spending increase this month?
Show the food transactions behind that change.
What drove my income change this month?
How did my finances change this month?
Which account changed the most?
```

Run it with `--scenario variance-insight --seed --reset-session`. A `require_complete` insight must decline a calculation when coverage is incomplete. A `disclose` insight may answer, but must state that its coverage is partial or unavailable.

## Safe Smoke Areas

- Greeting, capabilities, and unsupported-capability decline.
- Linked accounts, balances, and read-only refresh follow-ups.
- Beneficiary list and ambiguous beneficiary selection.
- Transaction query pagination, detail drill-down, recheck, and time rescope.
- Single transfer confirmation edits, PIN authorization, processing response, and receipt summary.
- Airtime/data self-purchase with source account selection.
- Mixed batch transfer edits, recipient review, funding review, and final confirmation.
- Pooled funding suggestion, explicit two-source split, and rejection of over-cap pooling.
- Expired confirmation/session cleanup before `yes`, greeting, or fresh task input.
