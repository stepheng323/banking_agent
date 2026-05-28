# Domain Workers

This package owns domain workers called by the orchestrator after task planning. Each domain should keep its worker small and place reusable behavior in its local nodes, services, validators, or shared worker helpers.

## Domain Map

- `query/`: transaction and account query handling, local mirror reads, continuations, grounding, and answer presentation.
- `transfer/`: money-transfer extraction, resolution, funding, confirmation, authorization, scheduling, and execution.
- `airtime/`: airtime purchase extraction, account selection, confirmation, validation, and execution.
- `data/`: data bundle purchase extraction, plan selection, account selection, confirmation, validation, and execution.
- `account/`: account balance, listing, default-account updates, and account-linking task handling.
- `beneficiary/`: saved beneficiary listing, creation, deletion, and post-transfer save suggestions.
- `support/`: support intent handling, transaction reference resolution, status/retry/reversal/fraud/receipt flows, and ticket routing.
- `faq/`: FAQ retrieval and support handoff.
- `onboarding/`: onboarding task service entry points used by worker flows.
- `__shared__/`: helpers shared across multiple domain workers, such as account selection, beneficiary suggestions, response synthesis, scheduling, source-account guards, and common validation.

## Navigation Rules

- Start at a domain `worker.py` to understand how the orchestrator enters that worker.
- Keep domain-specific helpers inside the domain package unless at least two domains use them.
- Put cross-domain worker helpers under `__shared__/`; shared runtime services that are not worker-specific belong in `shared/`.
- Import concrete modules directly and avoid compatibility wrappers for old worker paths.
