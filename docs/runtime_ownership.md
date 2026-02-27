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

4. `messaging-worker` (Lambda)
- Owns async messaging queues:
  - `notification.send`
  - `actionable_message.send`
  - `receipt.process`

## Guardrails

- Chat-critical queues are consumed only by ECS chat worker.
- Async queues are consumed only by lambda workers.
- Webhook ingress is owned only by gateway lambda/API Gateway.
