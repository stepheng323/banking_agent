"""Context-frame follow-up and replay-modifier prompts."""

CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT = """You classify a multilingual user message as a semantic operation relative
to the latest displayed assistant result frame.

Return ONLY JSON for this schema:
- decision: answer_completeness | lookup_entity | show_details | filter_items | compare_items | select_item |
  explain_result | replay_tasks | edit_schedule | cancel_schedule | start_new_task | unclear
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- target_text: referenced displayed entity, label, bank, recipient, group, or visible target, else null
- requested_field: amount | bank | counterparty | date | network | phone | reference | status | null
- rank: largest | smallest | newest | oldest | null
- filters: object with optional transaction_type, status, direction, bank, counterparty
- selection_index: integer or null
- reason: short reason

Semantic operations:
1) answer_completeness: user asks whether the displayed result is exhaustive, complete, missing more items, whether
   that is all, or how many displayed items exist.
2) lookup_entity: user asks whether a named entity, expected item, remembered item, alternative, or missing item is
   part of the displayed result.
3) show_details: user asks for more details, full details, specific fields, or explanation of one or more displayed
   items.
4) filter_items: user asks to narrow the displayed result by an attribute, entity name, bank, amount, status, type, or
   other visible frame field.
5) compare_items: user asks to compare displayed items, groups, periods, balances, amounts, statuses, or which one is
   higher/lower/newer/older.
6) select_item: user selects an item from the displayed result by number, ordinal, label, or reference.
7) explain_result: user asks what the displayed result means, why it looks that way, or asks a conversational question
   about the displayed result as a whole.
8) replay_tasks: user asks to repeat, replay, redo, resend, or run again one or more transaction items from the
   displayed frame. This is only valid for transaction/receipt frames. If no specific item is referenced, it means
   every replayable transaction item in the displayed frame.
9) edit_schedule: user asks to change, update, reschedule, move, or modify one or more displayed scheduled
   transaction items. This is only valid for scheduled transaction frames.
10) cancel_schedule: user asks to cancel, delete, remove, stop, or disable one or more displayed scheduled
   transaction items. This is only valid for scheduled transaction frames.
11) start_new_task: user is starting a fresh banking/conversation task, not following up on the displayed frame.
12) unclear: not enough signal.

Rules:
- Be semantic and language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, French, and mixed input.
- Use the frame summary only as displayed surface context. Do not invent records or facts.
- For lookup_entity, filter_items, show_details, compare_items, or select_item, put only the referenced displayed
  entity/filter target in target_text, not the full sentence.
- For specific field questions, set requested_field to one of these English field labels even when the user asks in
  another language: bank, amount, counterparty, date, network, phone, reference, status.
- For account frames, questions about why a displayed account is pending/ready/approved/rejected are frame
  follow-ups, not transaction queries. Use explain_result or show_details with target_text set to the account/bank
  name and requested_field=status.
- For ranking questions, set rank to one of these English rank labels: largest, smallest, newest, oldest.
- For narrowing by displayed transaction type/status/direction/bank/counterparty, set filters. Use transaction_type
  for transfer, airtime, data, credit, debit, inflow, outflow when that is the user's target.
- For numeric/ordinal selection, set selection_index when clear.
- Do not classify money movement or mutations as frame follow-ups unless the user is only selecting from the
  displayed frame, asking to replay displayed transaction item(s), or asking to edit/cancel displayed scheduled
  transaction item(s).
- For replay_tasks, set target_text only when the user targets a subset such as a recipient, "airtime", amount,
  ordinal, bank, or label. Set selection_index for clear numeric/ordinal references.
- When a displayed frame exists, prefer one of the frame operations for comments/questions that can plausibly refer
  to that frame. Use start_new_task only when the user clearly asks for a fresh action or fresh read.
- For scheduled transaction frames, treat schedule management follow-ups like "change it to 9am", "move that one",
  "cancel it", and multilingual equivalents as edit_schedule or cancel_schedule, not generic start_new_task.
- For scheduled transaction frames, broad count/list/read questions like "do I have any scheduled transactions" are
  fresh reads; use start_new_task so the schedule worker can reload active schedules.
- If the user challenges, doubts, remembers, expects, or asks about a missing item from the displayed result,
  classify it as lookup_entity and set target_text to the missing or expected item.
- If the message is plausibly about the displayed frame but the operation is uncertain, use unclear instead of
  start_new_task.
- If the user asks to send, transfer, buy airtime/data, check balance, view transactions, create/update/delete
  something, or otherwise starts a fresh task, use start_new_task.
"""

CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Displayed frame: {context}
Message: \"\"\"{user_message}\"\"\"
"""

CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT = """Extract strict replay modifiers from a multilingual banking message.

This is NOT full transaction extraction. A displayed transaction/receipt is already the authoritative base.
You only identify explicit edits the user made while asking to replay it.

Return ONLY JSON for this schema:
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- amount: replacement transaction amount as a number, else null
- amount_evidence: exact phrase from the user's latest message supporting amount, else null
- source_account_reference: explicit source bank/account reference, else null
- source_account_evidence: exact phrase from the user's latest message supporting source_account_reference, else null
- narration: transfer narration/memo/note/purpose text, else null
- narration_evidence: exact phrase from the user's latest message supporting narration, else null
- reason: short reason

Rules:
1) Use ONLY the latest user message for modifier fields. Do not copy amount, bank, source, recipient, or narration
   from the displayed frame.
2) Extract only these replay edits: amount, source_account_reference, narration.
3) Never extract or change the recipient, recipient account, recipient bank, transaction type, or PIN/auth fields.
4) If a field is implied by the displayed transaction but not explicitly edited in the latest message, return null.
5) Evidence must be a direct phrase from the user's message. If you cannot point to a phrase, leave the field null.
6) Be semantic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, French, and mixed input.
7) Examples:
   - "again but 10k" -> amount=10000, amount_evidence="10k"
   - "again from Zenith" -> source_account_reference="Zenith", source_account_evidence="Zenith"
   - "again for rent" -> narration="rent", narration_evidence="rent"
   - "tun se lati Zenith fun rent" -> source_account_reference="Zenith"; narration="rent"
   - "encore avec dix mille depuis Zenith pour loyer" -> amount=10000; source_account_reference="Zenith";
     narration="loyer"
8) If the user is not editing replay fields, return all modifier fields null with low or moderate confidence.
"""

CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Displayed frame: {context}
Message: \"\"\"{user_message}\"\"\"
"""

__all__ = [
    "CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT",
    "CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE",
    "CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT",
    "CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE",
]
