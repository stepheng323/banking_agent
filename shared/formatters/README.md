# Formatters

This package owns deterministic text formatting and presentation helpers shared across workers and webhooks.

## Module Families

- `accounts.py`, `beneficiary.py`, `recipient_display.py`, `recipient_prompt_names.py`: account and recipient display helpers.
- `airtime.py`, `data.py`, `transfer_summary.py`, `transfer_input_prompts.py`, `transfer_notifications.py`, `transfer_funding_plan.py`, `transfer_multi_source.py`: transaction-specific prompts, summaries, and notifications.
- `transaction_confirmation_copy.py`, `transaction_slot_prompts.py`, `transaction_intent_lines.py`, `transaction_amounts.py`: cross-transaction confirmation, slot prompt, intent-line, and amount copy.
- `transaction_copy_common.py`, `transaction_copy_context.py`: shared transaction copy primitives and context derivation.
- `query_transaction_copy.py`: query and support-friendly transaction evidence/status copy.
- `support_transaction_copy.py`: support-specific transaction status wording.
- `multi_action_summary.py`, `batch_transfer_summary.py`, `batch_funding.py`: multi-task and batch summaries.
- `auth_reason.py`, `confirmation.py`, `funding.py`, `missing_detail_prompts.py`: reusable prompt and reason formatting.
- `receipt.py`: receipt image formatting entry point.
- `currency.py`: money display helpers.

## Navigation Rules

- Import formatter functions from their concrete modules.
- Keep user-facing deterministic copy here; LLM prompts belong under the relevant prompt package or service.
- Put shared transaction primitives in `transaction_copy_common.py` or `transaction_copy_context.py`.
- Add a new formatter module only when a copy surface has clear ownership that does not fit an existing family.
