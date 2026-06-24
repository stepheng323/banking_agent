# Nenya AI Screen Recording Script

Use this to record a focused product demo. You do not need Mono or Flutterwave production access because the recording uses seeded accounts and simulated provider execution.

Target length: 4-6 minutes.

## Before Recording

1. Start the local application stack.
2. Use an already onboarded test user.
3. Seed the user with demo accounts and beneficiaries:

```bash
PYTHONPATH=. uv run python -m scripts.seed_user_test_data --phone <TEST_PHONE_E164>
```

4. Confirm the user has:
   - First Bank, GTBank, and Access Bank accounts
   - Tolu Access, Tolu GTB, and Tolu First beneficiaries
   - A six-digit test PIN you know
5. Clear old conversation state or start a clean chat.
6. Hide terminals, logs, secrets, phone numbers, and developer tools from the recording.

Do one private rehearsal before recording. Use the same user, prompts, and order.

## Opening

**Voiceover**

> This is Nenya AI, a conversational banking assistant designed for Nigerian users. I can interact with it naturally instead of navigating several banking screens.

**Type**

```text
What can you do?
```

**Show**

- Transfers
- Account balances and transaction queries
- Airtime and data
- Beneficiaries, schedules, receipts, and support

Do not read every capability aloud. Leave the response visible for two seconds.

## Accounts And Balances

**Voiceover**

> The assistant understands the accounts connected to this user and can answer questions across multiple banks.

**Type**

```text
Show my accounts and balances
```

Pause on the account list.

**Type**

```text
How much do I have altogether?
```

**What this demonstrates**

- Linked-account awareness
- Cross-account balance aggregation
- Contextual follow-up without repeating “my accounts”

## Transaction Intelligence

**Voiceover**

> It can also search and explain transaction history conversationally.

**Type**

```text
Show my recent transactions
```

After the results appear, type:

```text
Show only the debits
```

Then:

```text
Which one was the largest?
```

**What this demonstrates**

- Transaction retrieval
- Follow-up filtering
- Context retention
- Simple financial analysis without manual spreadsheet work

## Safe Transfer Flow

**Voiceover**

> Money movement is structured. Nenya collects missing information, resolves the beneficiary, lets me edit the instruction, and requires confirmation before execution.

**Type**

```text
Send 5,000 naira to Tolu
```

The seeded data should produce multiple Tolu matches.

**Type**

```text
Tolu Access
```

When the transfer review appears, type:

```text
Change it to 3,500
```

Pause on the updated review.

**Type**

```text
Yes
```

When prompted, enter the six-digit test PIN through the normal secure PIN flow.

**Voiceover**

> The transfer is simulated for this demo. The important part is the control flow: beneficiary resolution, editable review, explicit confirmation, authorization, and idempotent execution.

Pause on the processing or completion message and receipt.

## Mixed Request

Start this section only after the transfer flow has fully completed.

**Voiceover**

> A user can also combine different banking actions in one natural-language request.

**Type**

```text
Send 2,000 naira to Tolu GTB and buy 1,000 naira MTN airtime for me
```

Pause on the batch review.

Do not execute this batch unless the rehearsal proved the entire path is stable. The review itself demonstrates task decomposition and coordinated confirmation.

**What this demonstrates**

- Multiple intents in one message
- Typed task planning
- Batch review
- Shared confirmation and authorization controls

## Capability Boundary

Cancel any pending batch before continuing.

**Voiceover**

> The assistant also needs to understand what the user did not ask for. It should not convert a vague request for money into a zero-naira transfer.

**Type**

```text
I need money abeg
```

**Expected behavior**

- It explains that it cannot lend or provide money
- It may offer supported alternatives
- It does not ask for a recipient account
- It does not create a transfer
- It does not show a zero-naira task

## Closing

**Voiceover**

> This demo uses synthetic financial data and simulated provider execution. It shows the conversational experience, orchestration, safety controls, and multi-account design. Production provider access and regulatory onboarding are separate launch steps.

End on either the capability response or the account summary. Avoid ending on a PIN screen, error, or pending transaction.

## Short Version

For a 90-second recording, use only:

```text
What can you do?
Show my accounts and balances
Show my recent transactions
Show only the debits
Send 5,000 naira to Tolu
Tolu Access
Change it to 3,500
I need money abeg
```

Stop the transfer at the updated review. This avoids waiting for workers or provider simulation during a short recording.

## Recovery Lines

If a response differs slightly, continue naturally:

```text
Show my linked accounts
Show my recent debit transactions
Use Tolu Access
Change the transfer amount to 3,500 naira
Cancel this transaction
Start over
```

If the assistant enters an unexpected state, stop the take, clear the session, reseed the user, and restart. Do not debug during the recording.
