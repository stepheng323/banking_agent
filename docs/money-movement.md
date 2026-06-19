# Money Movement

This document covers transfers, pooled funding, recipient review, confirmation, authorization, execution, refunds, receipts, and reconciliation.

Use seeded users and sandbox/mock providers for local and smoke flows. Do not use real-money accounts in local development.

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Confirmation | The user approval step after recipient, amount, source, and narration are ready to show. |
| Authorization | The security step after confirmation, usually PIN-based. |
| Idempotency key | The stable key that prevents duplicate financial execution during retries. |
| Funding review | The step that checks whether selected/default accounts can fund the request. |
| Pooled funding | Funding one request from multiple source accounts. |
| Refund | A compensating flow for failed or reversed financial work. |
| Reconciliation | Background checks that compare internal records with provider-side status. |
| Receipt | A rendered transaction proof/status message delivered to the user. |

Example: a transfer is not PIN-ready just because it has an amount and recipient. It must pass funding review, show final confirmation, then bind authorization to the exact idempotency keys before async execution.

## Transfer Lifecycle

```text
user request
-> planner task
-> recipient resolution / review
-> funding review
-> final confirmation
-> PIN/auth
-> async debit/payout/bill execution
-> reconciliation / completion notification
```

The user only reaches PIN/auth after recipient details are resolved, funding is feasible, and final confirmation has been rendered.

## Recipient Resolution And Review

Transfers must resolve destination identity before funding becomes actionable.

Key rules:

- destination account/bank edits clear prior recipient resolution and stale funding;
- batch transfers wait until all active recipient legs are resolved/reviewed before batch funding review;
- resolver mode must match funding mode when necessary;
- provider name mismatches should trigger review instead of silently overwriting reviewed display names.

Recipient review gives the user a chance to correct account identity before money movement continues.

## Funding Review

Funding review checks whether the selected/default source account can cover the transaction. If not, it can suggest adjustments without silently authorizing a new funding setup.

Behavior:

- default account is the anchor when the user did not name a source;
- explicit source selection outranks default;
- pooled funding is opt-in unless the user explicitly names all source accounts;
- source choices are resolved against linked accounts before applying;
- amount/source/recipient edits invalidate stale funding and final confirmation.

The system currently limits pooled funding to two source accounts for a request. If the two-account cap cannot cover the batch, the user is prompted to reduce an amount, remove a transfer, choose sources, or cancel.

## Confirmation And Authorization

Final confirmation is the only PIN-ready state. It must show:

- resolved recipients;
- destination bank/account;
- amount and total;
- source account or accepted funding breakdown;
- narration when present.

PIN callbacks are bound to the exact task idempotency keys in the pending interrupt. A broad `pin_verified=True` flag is not sufficient to execute any task.

## Execution

Financial execution is async and idempotent. The chat worker publishes financial work to Redis streams; transaction-worker consumes and owns provider-side execution.

Common async streams:

| Purpose | Topic | Stream |
|---|---|---|
| direct transfer | `transaction.execute` | `async:transactions` |
| funding debit | `funding.process` | `async:funding` |
| payout | `payout.process` | `async:payouts` |
| bill purchase | `bill.fulfill` | `async:bill_fulfillment` |
| refund | `refund.process` | `async:refunds` |
| receipt render | `receipt.process` | `async:receipts` |
| final notification | `notification.send` | `async:notifications` |

Provider calls are bounded by idempotency keys and persisted transaction/funding rows. Completion, failure, and pending statuses are delivered through the notification pipeline.

## Refunds And Reconciliation

If funding succeeds but payout/bill fulfillment fails, the transaction should not be shown as successful. Refund jobs and reconciliation loops carry the system toward terminal provider truth.

Reconciliation areas include:

- funding status;
- payout status;
- refund status;
- bill fulfillment status;
- transaction debit status;
- ledger postings and exposure findings.

Ledger corrections should be represented as reversal or adjustment entries, not mutation of existing ledger lines.

## Receipts

Receipt generation must resolve transaction ownership and status before rendering. Failed, pending, processing, reversed, refunded, or non-owned transactions should not produce a successful receipt image.

Receipt jobs should carry references, not trusted full transfer payloads.

## Main Code Paths

| Area | Path |
|---|---|
| Transfer worker | `banking/transfers/worker.py` |
| Transfer nodes | `banking/transfers/nodes/` |
| Funding coordination | `apps/chat/src/agent/orchestrator/workflows/execution/funding/` |
| Confirmation gate | `apps/chat/src/agent/orchestrator/workflows/execution/confirmation/` |
| Transaction runtime | `banking/transactions/runtime/` |
| Receipt consumer | `apps/receipt/src/consumer.py` |
| Authorization context | `banking/security/authorization_context.py` |
