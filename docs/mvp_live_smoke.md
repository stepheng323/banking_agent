# MVP Live Smoke Checklist

Use this checklist for Telegram and WhatsApp before inviting testers. Run each flow with a seeded test user.
Capture the transcript, timestamp, channel, and phone number for any failure.

## Ground Rules

- Do not use real money accounts in smoke tests.
- Keep batch transactions to 5 tasks or fewer.
- Keep pooled funding to 2 source accounts or fewer.
- A stale transaction confirmation should not be executable after `PENDING_TRANSACTION_TTL`.
- Any money movement must re-render confirmation and require the existing PIN/authorization path.

## Setup

1. Start the local stack or deploy target.
2. Confirm gateway, chat worker, transaction worker, and receipt worker are healthy.
3. Confirm the test user has:
   - at least 3 linked accounts, with at least 2 ready mandates
   - at least 3 saved beneficiaries with overlapping names
   - recent transactions available for query follow-ups
   - a known test PIN

## Automated Dry-Run

Use the automated smoke runner when you do not have a tester available. It runs the real orchestrator against
your configured DB, Redis, providers, and LLMs, but it does not send Telegram or WhatsApp messages.

```bash
PYTHONPATH=. uv run --extra all python -m scripts.live_smoke \
  --phone 2348162511023 \
  --channel telegram \
  --seed \
  --reset-session
```

For the longer mixed-transaction path:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.live_smoke \
  --phone 2348162511023 \
  --channel telegram \
  --scenario mvp \
  --seed \
  --reset-session
```

For the query-only conversational path:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.live_smoke \
  --phone 2348162511023 \
  --channel telegram \
  --scenario query \
  --seed \
  --reset-session
```

For the deeper query hardening path:

```bash
PYTHONPATH=. uv run --extra all python -m scripts.live_smoke \
  --phone 2348162511023 \
  --channel telegram \
  --scenario query-deep \
  --seed \
  --reset-session
```

The runner prints a transcript-style log and marks each turn as pass or fail using loose assertions. Treat failures as
signals to inspect the transcript, not as a replacement for final human channel testing.

## Core Conversation

1. Send: `Hi`
   - Expected: greeting/help response, no stale transaction resumed.
2. Send: `What can you do?`
   - Expected: banking capability response.
3. Send a casual non-banking message.
   - Expected: brief conversational response plus banking redirect.

## Beneficiaries

1. Send: `Show my beneficiaries`
   - Expected: saved beneficiary list.
2. Send: `Is that all?`
   - Expected: grounded answer from the displayed list.
3. Send: `What about <missing name>?`
   - Expected: says it was not in the displayed beneficiaries, not a generic fallback.
4. Send: `Send 2k to <ambiguous beneficiary name>`
   - Expected: asks which matching beneficiary, then resolves selected reference.

## Accounts

1. Send: `Show my accounts`
   - Expected: account list with mandate/default indicators.
2. Send: `Which one is GTBank?`
   - Expected: GTBank details from the displayed list.
3. Send: `Why is <pending bank> pending?`
   - Expected: status-specific explanation and next action.
4. Send: `How do I complete it?`
   - Expected: instruction based on that mandate status.

## Query Surfaces

1. Send: `Show my recent transactions`
   - Expected: first page of recent transactions with pagination hint/buttons where supported.
2. Send: `Next` or use the next button.
   - Expected: next page, not a repeated first page.
3. Send: `Show the second one`
   - Expected: details for visible second transaction.
4. Send: `What bank was that?`
   - Expected: answers from selected transaction detail.
5. Send: `Now show the 3rd transaction`
   - Expected: resolves against the prior transaction list when still in query memory.
6. Send: `What about credits?`
   - Expected: filters/refines query result to credits.
7. Send a fresh request: `Send 1k airtime to me`
   - Expected: exits query follow-up and starts airtime flow.

## Single Transfer

1. Send: `Send 2k to <saved beneficiary>`
   - Expected: transfer confirmation with recipient, bank/account, source account.
2. Send: `The narration should be urgent 2k`
   - Expected: confirmation re-renders with narration, no execution yet.
3. Authorize with PIN.
   - Expected: processing response and final receipt/summary.

## Airtime And Data

1. Send: `Buy me 1k airtime`
   - Expected: self phone fallback or phone prompt, then confirmation.
2. Authorize with PIN.
   - Expected: airtime summary uses phone/network only, not beneficiary name.
3. Send: `Buy 500 data for me`
   - Expected: data plan selection or confirmation, then PIN authorization.

## Mixed Batch

1. Send: `Send 2k to <beneficiary> and buy me 1k airtime`
   - Expected: one combined confirmation.
2. Send: `The transfer narration should be groceries`
   - Expected: only transfer narration changes.
3. Send: `Remove airtime`
   - Expected: confirmation re-renders as transfer only.
4. Send: `Sorry add it back`
   - Expected: airtime restored into the same batch.
5. Send: `Change airtime amount to 2k`
   - Expected: only airtime amount changes.
6. Authorize with PIN.
   - Expected: one batch summary covering every task and any failures.

## Pooled Funding

1. Send a transfer amount larger than one ready source account can cover.
   - Expected: pooled funding suggestion with clear breakdown.
2. Send: `Use Access and First Bank`
   - Expected: re-render with max 2 sources.
3. Send: `Make it 20k from Access and 15k from First Bank`
   - Expected: explicit split applied or clear clarification if infeasible.
4. Try 3 source accounts.
   - Expected: rejects/clarifies because max pooled funding sources is 2.

## Replay

1. Quote a completed mixed transaction summary and send: `Send again`
   - Expected: new confirmation for all replay-safe tasks.
2. Quote a partial-failure summary and send: `Retry the failed one`
   - Expected: new confirmation only for failed leg.
3. Send: `Retry airtime`
   - Expected: new confirmation only for airtime leg.
4. Confirm replay.
   - Expected: new idempotency keys, normal PIN path, no old transaction IDs reused.

## Expired Transaction Session

1. Start a transfer and wait longer than `PENDING_TRANSACTION_TTL`.
2. Send: `Yes`
   - Expected: standard transaction-expired response; no execution.
3. Start another transfer and wait longer than `PENDING_TRANSACTION_TTL`.
4. Send: `Hi`
   - Expected: stale transaction silently clears and normal greeting routes.
5. Start another transfer and wait longer than `PENDING_TRANSACTION_TTL`.
6. Send a fresh request: `Show my accounts`
   - Expected: stale transaction silently clears and account list routes.

## Failure Reporting Format

Use this format for every bug:

```text
Channel:
Phone:
Timestamp:
Transcript:
Expected:
Actual:
Screenshots/log excerpt:
```
