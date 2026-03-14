# Runtime Ownership

## Production/Staging Runtime Split

1. `gateway-lambda` (API Gateway ingress)

- Owns webhook ingress endpoints.
- Publishes inbound events to SNS/SQS via queue publisher.

2. `core-chat-worker` (ECS service)

- Owns chat-critical queue consumption:
  - `message.received`
  - `flow_event.process`

3. `transaction-worker` (Lambda)

- Owns async financial queues:
  - `transaction.execute`
  - `funding.process`
  - `payout.process`
  - `refund.process`
  - ``

4. `receipt-worker` (Lambda)

- Owns async messaging queues:
  - `notification.send`
  - `actionable_message.send`
  - `receipt.process`

## Guardrails

- Chat-critical queues are consumed only by ECS chat worker.
- Async queues are consumed only by lambda workers.
- Webhook ingress is owned only by gateway lambda/API Gateway.
- During VPS migration, parallel infrastructure is allowed but active ownership must still be single-writer/single-consumer.
- When `ASYNC_TRANSPORT=aws`, receipt jobs can move independently but transaction, funding, payout, and refund share one SQS queue and therefore cut over as one ownership unit.
- When `ASYNC_TRANSPORT=redis`, async topics are split by Redis Stream and can be owned independently per worker domain.

## Migration Flags

The repo now supports explicit runtime ownership via environment flags:

- `RUNTIME_STACK_ROLE`
- `ENABLE_WEBHOOK_INGRESS`
- `ENABLE_CHAT_CONSUMERS`
- `ENABLE_TRANSACTION_WORKER`
- `ENABLE_FUNDING_WORKER`
- `ENABLE_PAYOUT_WORKER`
- `ENABLE_REFUND_WORKER`
- `ENABLE_RECEIPT_WORKER`
- `ENABLE_OUTBOUND_SENDER`
- `ASYNC_TRANSPORT`
- `CHAT_TRANSPORT`

See [vps_parallel_migration.md](/home/abiodun/dev/personal/banking_agent/docs/vps_parallel_migration.md) for cutover and rollback.

## Configuration Ownership

- Runtime configuration is delivered from SSM Parameter Store with prefix `/banking-agent/<env>/`.
- `config-ssm` Terraform module is the canonical source for parameter creation.
- ECS `core-chat-worker` reads secrets via task definition `secrets` (SSM ARN references).
- Lambda workers (`transaction-worker`, `receipt-worker`, `gateway-lambda`) receive env values from SSM at deploy time.
- SSM and KMS IAM permissions are scoped to Terraform-managed parameter ARNs.
- `core-chat-worker` may omit `INTERRUPT_ROUTER_MODEL`; runtime defaults it to `PLANNER_MODEL`.
- Supplying a dedicated `INTERRUPT_ROUTER_MODEL` is recommended for latency and routing isolation.
