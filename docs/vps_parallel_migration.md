# VPS Parallel Migration

## Goal

Run a VPS stack with explicit ownership controls. This document now also covers the final AWS-free VPS mode.

## Managed Data Stores

This VPS stack assumes Postgres and Redis are externally hosted.

Required env vars in `.env`:

- `DATABASE_URL`
- `REDIS_URL`
- `APP_DOMAIN`
- `ACME_EMAIL`

Optional integration overrides:

- `MONO_USE_MOCK=true|false`

Operational requirements:

- the VPS must have network access to both managed services
- firewall/allowlist should admit only the VPS public IP where supported
- keep the VPS, Postgres, and Redis in nearby regions to limit latency
- enable TLS on the provider side if available
- DNS for `APP_DOMAIN` must point to the VPS public IP
- ports `80` and `443` must be open on the VPS firewall for ACME + HTTPS

## Recommended Runtime Mode

For the current production cutover:

- set `CHAT_TRANSPORT=redis`
- set `ASYNC_TRANSPORT=aws`
- point `DATABASE_URL` and `REDIS_URL` at the managed services

In this mode:

- chat ingress and flow events use Redis Streams
- durability-critical async jobs stay on SNS/SQS
- compute leaves AWS, but durable async transport remains on AWS

## Runtime Flags

The stack now uses only transport/runtime envs that affect actual infrastructure behavior:

- `ASYNC_TRANSPORT`
- `CHAT_TRANSPORT`

## Bring-Up

1. Start the VPS stack:

```bash
./deploy-stack.sh remote
```

This canonical path syncs the stack assets, pulls the pinned runtime images, and restarts the VPS stack.
The compose file does not start local Postgres or Redis.
It uses the managed `DATABASE_URL` and `REDIS_URL` from `.env`.
It expects `APP_DOMAIN` and `ACME_EMAIL` so Caddy can provision TLS automatically.

2. Confirm stack health:

```bash
curl http://<host>/health
curl http://<host>/receipt/health
curl http://<host>/transaction/health
```

3. Confirm logs show service startup:

- `gateway_service_starting`
- `transaction_worker_service_starting`
- `receipt_service_starting`

## GitHub Actions Deploy

The repo now supports VPS deploys through [deploy-vps.yml](/home/abiodun/dev/personal/banking_agent/.github/workflows/deploy-vps.yml).

Required GitHub repository secrets:

- `VPS_SSH_PRIVATE_KEY`
- `GHCR_PULL_USERNAME`
- `GHCR_PULL_TOKEN`

Optional GitHub repository variables:

- `VPS_HOST`
- `VPS_SSH_USER`
- `VPS_APP_DIR` (defaults to `/srv/banking_agent`)
- `VPS_SSH_PORT` (defaults to `22`)

Workflow behavior:

- runs lint, mypy, pytest
- runs VPS runtime image smoke checks
- builds and pushes the VPS runtime images
- invokes `./deploy-stack.sh remote`
- does not overwrite the VPS `.env` unless a local `.env` is present for the caller
- verifies `/health`, `/transaction/health`, and `/receipt/health`

Because the workflow syncs the checked-out workspace directly, the VPS does not need GitHub deploy credentials or `git pull` access.

## Cutover Order

### Final VPS Mode

- `ASYNC_TRANSPORT=aws`

After DNS/webhook cutover, disable the corresponding AWS runtimes and only keep the VPS services running.

## Rollback

1. Stop the affected VPS service.
2. Re-enable the same ownership on AWS if you still keep AWS available.
3. Confirm only one side is consuming queues or sending replies.

## Guardrails

- Do not run both ingress owners at once.
- Do not run both chat consumers at once.
- Do not run both receipt workers at once.
- Do not run AWS and VPS against the same logical async topics at the same time.
- Chat messages older than `CHAT_MESSAGE_MAX_AGE_SECONDS` are dropped by the VPS chat worker.
