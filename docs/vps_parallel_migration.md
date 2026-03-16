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

These environment flags now control ownership explicitly:

- `RUNTIME_STACK_ROLE`
- `ASYNC_TRANSPORT`
- `CHAT_TRANSPORT`
- `ENABLE_WEBHOOK_INGRESS`
- `ENABLE_CHAT_CONSUMERS`
- `ENABLE_TRANSACTION_WORKER`
- `ENABLE_FUNDING_WORKER`
- `ENABLE_PAYOUT_WORKER`
- `ENABLE_REFUND_WORKER`
- `ENABLE_RECEIPT_WORKER`
- `ENABLE_OUTBOUND_SENDER`

## Bring-Up

1. Start the VPS stack:

```bash
docker compose -f docker-compose.vps.yml up -d --build
```

This compose file does not start local Postgres or Redis.
It uses the managed `DATABASE_URL` and `REDIS_URL` from `.env`.
It expects `APP_DOMAIN` and `ACME_EMAIL` so Caddy can provision TLS automatically.

2. Confirm passive ownership:

```bash
curl http://<host>/health
curl http://<host>/core/health
curl http://<host>/receipt/health
curl http://<host>/transaction/health
```

3. Confirm logs show passive mode:

- `gateway_service_starting`
- `transaction_worker_service_starting`
- `receipt_service_starting`

## GitHub Actions Deploy

The repo now supports VPS deploys through [deploy-vps.yml](/home/abiodun/dev/personal/banking_agent/.github/workflows/deploy-vps.yml).

Required GitHub repository secrets:

- `VPS_HOST`
- `VPS_SSH_USER`
- `VPS_SSH_PRIVATE_KEY`
- `VPS_SSH_KNOWN_HOSTS`

Optional GitHub repository variables:

- `VPS_APP_DIR` (defaults to `/srv/banking_agent`)
- `VPS_SSH_PORT` (defaults to `22`)

Workflow behavior:

- runs lint, mypy, pytest
- runs VPS runtime image smoke checks
- syncs the checked-out repo to the VPS over SSH
- does not overwrite the VPS `.env`
- does not overwrite `whatsapp_flow_private_key.pem`
- runs `docker compose -f docker-compose.vps.yml up -d --build`
- verifies `/health`, `/core/health`, `/transaction/health`, and `/receipt/health`

Because the workflow syncs the checked-out workspace directly, the VPS does not need GitHub deploy credentials or `git pull` access.

## Cutover Order

### Final VPS Mode

- `ENABLE_WEBHOOK_INGRESS=true`
- `ENABLE_CHAT_CONSUMERS=true`
- `ENABLE_TRANSACTION_WORKER=true`
- `ENABLE_FUNDING_WORKER=true`
- `ENABLE_PAYOUT_WORKER=true`
- `ENABLE_REFUND_WORKER=true`
- `ENABLE_RECEIPT_WORKER=true`
- `ENABLE_OUTBOUND_SENDER=true`
- `ASYNC_TRANSPORT=aws`

After DNS/webhook cutover, disable the corresponding AWS runtimes.

## Rollback

1. Disable the affected VPS ownership flag.
2. Re-enable the same ownership on AWS if you still keep AWS available.
3. Confirm only one side is consuming queues or sending replies.

## Guardrails

- Do not run both ingress owners at once.
- Do not run both chat consumers at once.
- Do not run both receipt workers at once.
- Do not run AWS and VPS against the same logical async topics at the same time.
- Chat messages older than `CHAT_MESSAGE_MAX_AGE_SECONDS` are dropped by the VPS chat worker.
