# Runtime Ownership

## Active Production Shape

The supported deployment model is now VPS-only.

Active services in the runtime stack:

1. `gateway`

- Owns webhook ingress endpoints.
- Publishes inbound work into the configured chat/async transports.

2. `chat-worker`

- Owns chat-critical queue consumption:
  - `message.received`
  - `flow_event.process`

3. `transaction-worker`

- Owns async financial queues:
  - `transaction.execute`
  - `funding.process`
  - `payout.process`
  - `payout.reconcile`
  - `refund.process`

4. `receipt-worker`

- Owns async messaging and receipt queues:
  - `notification.send`
  - `receipt.process`

## Guardrails

- Webhook ingress is owned only by `gateway`.
- Chat-critical queues are consumed only by `chat-worker`.
- Financial async queues are consumed only by `transaction-worker`.
- `transaction-worker` also owns the periodic payout reconciliation loop for stale Flutterwave payouts.
- Receipt and outbound messaging queues are consumed only by `receipt-worker`.
- Actionable message persistence remains inside `DeliveryService` during notification and receipt delivery.
- Do not run multiple stacks against the same logical queue ownership.

## Runtime Switching

Service presence is the ownership switch. If a role should not run, stop that service.

See [vps_parallel_migration.md](vps_parallel_migration.md) for stack bring-up and cutover notes.

## Configuration Ownership

- Runtime configuration is delivered through the VPS `.env`.
- The compose stack mounts AWS credentials from the host only when the configured transport/providers still require AWS access.
- `chat-worker` may omit `INTERRUPT_ROUTER_MODEL`; runtime defaults it to `PLANNER_MODEL`.
- Supplying a dedicated `INTERRUPT_ROUTER_MODEL` is still recommended for latency and routing isolation.
