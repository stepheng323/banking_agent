"""Compact prompt for one pending transfer amendment."""

TRANSFER_AMENDMENT_PROMPT = """Interpret one user reply about one already-known pending transfer. Return only JSON.

operation:
- update: change amount, recipient, destination bank/account, source account, balance percentage/all, funding split,
  or narration.
- confirm/cancel: explicit approval or cancellation.
- unrelated: a clearly different request. unclear: meaning cannot be safely grounded.

Amount changes use amount_mutation against current_pending_amount. Use set for a new total, add/subtract for a naira
delta, and multiply for transformations: double=2, triple=3, half=0.5, increase 10%=1.1, reduce 20%=0.8.
Keep compound steps in user order. Never calculate the final amount.

Balance-derived wording uses transfer_percentage or transfer_all, not amount_mutation. Capture recipient/source changes,
up to two source accounts, explicit source splits, and narration only when stated. Do not invent fields from context.
Scheduling and recurrence are parsed deterministically by the caller. Ignore those words while extracting any other
single-transfer changes, and do not mark the turn broad merely because scheduling is also present. Set
requires_broad_interpretation=true for batch or multi-recipient edits, mixed requests, image interpretation,
unsupported fields, or anything else that needs broader orchestration. Acknowledgment is optional and under eight
words. Understand English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed wording semantically.
"""

__all__ = ["TRANSFER_AMENDMENT_PROMPT"]
