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

## Configuration Ownership

- Runtime configuration is delivered from SSM Parameter Store with prefix `/banking-agent/<env>/`.
- `config-ssm` Terraform module is the canonical source for parameter creation.
- ECS `core-chat-worker` reads secrets via task definition `secrets` (SSM ARN references).
- Lambda workers (`transaction-worker`, `messaging-worker`, `gateway-lambda`) receive env values from SSM at deploy time.
- SSM and KMS IAM permissions are scoped to Terraform-managed parameter ARNs.
