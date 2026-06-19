# Product Domains

Product-owned banking behavior lives under `banking/`. The chat app owns orchestration and dispatch; domain modules own product rules, parsing, validation, presentation, and worker outcomes.

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Product domain | A banking capability area, such as accounts, transfers, beneficiaries, or transaction queries. |
| Action | A user-requested mutation or command, such as set default account, delete beneficiary, or cancel schedule. |
| Read/query flow | A flow that retrieves or summarizes information without changing banking state. |
| Mutation flow | A flow that changes state or starts money movement and usually needs stricter confirmation/auth boundaries. |
| Presentation surface | The rendered mobile-friendly message body or list that the user sees. |
| Worker outcome | A structured result from a domain worker, such as needs input, needs confirmation, ok, or failed. |

Example: listing beneficiaries is a read flow that produces a presentation surface. Deleting a beneficiary is a mutation flow and should ask clarification if the user reference is ambiguous.

## Accounts

Accounts cover linked-account lists, balances, default source changes, unlinking, relinking, and onboarding/account-linking flows.

Key paths:

- `banking/accounts/management/worker.py`
- `banking/accounts/management/formatter.py`
- `banking/accounts/onboarding/`

Important behavior:

- account list/balance surfaces use structured `body_blocks`;
- readiness labels should be user-facing, not raw provider status;
- balance and account refresh follow-ups should refetch live state when the previous surface is read-only.

## Beneficiaries

Beneficiaries cover saved recipient listing, alias matching, delete/update flows, and post-transfer save suggestions.

Key paths:

- `banking/beneficiaries/worker.py`
- `banking/beneficiaries/formatter.py`

Important behavior:

- list/count preview surfaces render mobile-friendly blocks;
- fuzzy alias matching helps route transfer recipients;
- ambiguous beneficiary references should ask clarification instead of guessing.

## Transaction Query

Transaction query covers transaction search, list views, analytics, summaries, fact questions, evidence lists, pagination, and follow-up continuations.

Key paths:

- `banking/transactions/query/worker.py`
- `banking/transactions/query/continuations/`
- `banking/transactions/query/presentation/`
- `banking/transactions/services/unified_transactions.py`

Important behavior:

- queries use a coverage-aware local transaction mirror;
- active query continuations are semantically routed before parser recovery;
- repeat/recheck reruns the current query instead of replaying stale memory;
- empty results should render natural user copy, not structural metadata.

## Transfers

Transfers cover extraction, recipient resolution, validation, funding review, confirmation, PIN/auth, execution, and summaries.

Key paths:

- `banking/transfers/worker.py`
- `banking/transfers/nodes/`
- `banking/transfers/extraction/`

Important behavior:

- funding must wait for recipient readiness;
- pooled funding is capped and opt-in;
- final confirmation is the PIN-ready boundary;
- stale funding/confirmation is invalidated by amount/source/recipient edits.

## Airtime And Data

Bills cover airtime and data purchase flows, source account selection, confirmation, scheduling, and execution.

Key paths:

- `banking/bills/airtime/worker.py`
- `banking/bills/airtime/nodes/`
- `banking/bills/data/worker.py`
- `banking/bills/data/nodes/`

Important behavior:

- self-purchase can resolve phone from user context;
- explicit phone/network corrections can trigger extraction;
- confirmation/auth boundaries mirror other money-moving tasks.

## Schedules

Schedules cover scheduled transaction list/find/cancel/edit surfaces and scheduled dispatch.

Key paths:

- `banking/transactions/shared/schedule_management.py`
- `apps/chat/src/agent/orchestrator/workflows/gate/stages/schedule_read_stage.py`

Important behavior:

- schedule list/find/candidate surfaces use structured message bodies;
- count mode stays compact;
- context-frame follow-ups should preserve schedule list behavior.

## Support And FAQ

Support handles transaction references, support tickets, receipt requests, unsupported capabilities, and escalation paths. FAQ handles policy-safe answers from the FAQ catalog.

Key paths:

- `banking/support/worker.py`
- `banking/faq/worker.py`
- `apps/chat/src/agent/orchestrator/capabilities/`

Important behavior:

- stale support/receipt context must not steal fresh money/account/query requests;
- terse selectors can remain deterministic, but non-terse stale-context turns should route semantically;
- unsupported capabilities should decline clearly and offer supported alternatives.

## Presentation

User-visible copy is mostly deterministic and lives near product domains or shared presentation helpers.

Key paths:

- `shared/messaging/body_blocks.py`
- `shared/messaging/presenters/`
- `banking/presentation/formatters/`

`body_blocks` provide channel-neutral structured message bodies that WhatsApp and Telegram presenters render into mobile-readable layouts.
