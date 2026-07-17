"""Context-frame follow-up and replay-modifier prompts."""

CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT = """You classify a multilingual user message as a semantic operation relative
to the latest displayed assistant result frame.

Return ONLY JSON for this schema:
- decision: answer_completeness | lookup_entity | show_details | filter_items | compare_items | select_item |
  explain_result | replay_tasks | edit_schedule | cancel_schedule | delete_beneficiary | unlink_account |
  set_default_account | relink_account | transfer_beneficiaries | start_new_task | unclear
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- target_text: referenced displayed entity, label, bank, recipient, group, or visible target, else null
- requested_field: amount | bank | counterparty | date | network | phone | reference | status | null
- rank: largest | smallest | newest | oldest | null
- filters: object with optional transaction_type, status, direction, bank, counterparty
- selection_index: integer or null
- read_subject: transaction | balance | linked_account | default_account | beneficiary | schedule | ticket | receipt |
  null. Set it for every banking read represented by the message
- read_response_shape: fact_bool | fact_count | fact_value | fact_status | fact_recap | surface_list |
  surface_detail | surface_paginated | surface_actionable | null
- page_action: next | previous | first | null
- balance_delta: object with scope_operation, bank_names, operation, response_shape; null outside balance follow-ups
- beneficiary_delta: object with operation, entity_name, bank_name, beneficiary_type; null outside retained
  beneficiary reads. operation is preserve | count | existence | list | detail
- account_lifecycle_delta: object with operation, bank_scope, bank_name; null outside linked/default-account reads.
  operation is preserve | count | existence | list | detail | readiness | default_identity; bank_scope is
  preserve | named | all
- set_scope_delta: object with operation, selection_indices, target_labels; null outside beneficiary, schedule, or
  linked-account set follow-ups
- set_amount_allocations: array of explicit {selection_index or target_label, amount}; use only for
  transfer_beneficiaries and include one allocation for every selected beneficiary
- schedule_edit_delta: sparse object containing only explicitly requested schedule edits such as amount,
  schedule_time_local, schedule_start_date, recurrence_type, recipient/source fields, phone/network, or plan; null
  outside edit_schedule
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
   transaction items. This is only valid for scheduled transaction frames. Extract every explicit change into
   schedule_edit_delta; do not calculate, copy, or invent unchanged values.
10) cancel_schedule: user asks to cancel, delete, remove, stop, or disable one or more displayed scheduled
   transaction items. This is only valid for scheduled transaction frames.
11) start_new_task: user is starting a fresh banking/conversation task, not following up on the displayed frame.
12) unclear: not enough signal.
13) delete_beneficiary: user asks to remove one or more beneficiaries from a beneficiary frame.
14) unlink_account/set_default_account/relink_account: user asks to apply that lifecycle action to selected linked
    accounts. set_default_account and relink_account require exactly one selected account.
15) transfer_beneficiaries: user asks to transfer to one or more selected beneficiaries. Return an explicit amount
    allocation for every selected beneficiary. Never split, copy, or infer an amount across recipients.

Rules:
- Be semantic and language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, French, and mixed input.
- Use the frame summary only as displayed surface context. Do not invent records or facts.
- For lookup_entity, filter_items, show_details, compare_items, or select_item, put only the referenced displayed
  entity/filter target in target_text, not the full sentence.
- For specific field questions, set requested_field to one of these English field labels even when the user asks in
  another language: bank, amount, counterparty, date, network, phone, reference, status.
- For account frames, questions about why a displayed account is pending/ready/approved/rejected are frame
  follow-ups, not transaction queries. Use explain_result or show_details with target_text set to the account/bank

Canonical read follow-ups:
- When context contains read(...), preserve its subject and filters.
- Set read_subject to the subject the latest message actually asks about. A named filter does not override an
  explicit subject noun: an account-linkage/readiness question is linked_account even when a beneficiary frame is
  active; a beneficiary-membership question is beneficiary. If read_subject differs from the retained frame, this
  is a typed read pivot, not a lookup inside the old result.
- For a retained beneficiary read, a question asking whether a specifically named person or alias is saved is a
  repository membership lookup, even when the preceding answer was only a total count. Return lookup_entity,
  target_text set to only that name or alias, and beneficiary_delta={operation: existence, entity_name: target}.
  Do not limit the lookup to rows in the fact frame and do not use set_scope_delta for a person who may not be in the
  last displayed result.
- Always return beneficiary_delta for a retained beneficiary read. A narrowed count uses operation=count; a
  membership question uses operation=existence; revealing matching rows uses operation=list. Include only explicit
  filter changes and preserve unrelated retained filters.
- Always return account_lifecycle_delta for a retained linked/default-account read. A request for the linked-account
  collection uses operation=list. Use bank_scope=all when the user asks for the linked accounts generally rather than
  the previously filtered bank; preserve keeps the old bank filter, and named replaces it with bank_name.
- When the user repeats, reaffirms, contrasts, or substitutes a bank on a retained balance or linked-account read,
  set filters.bank to the requested bank and preserve the prior response shape. This is a refinement of the same
  read whether it names the same bank again or a different bank that was not in the displayed result.
- A short elliptical follow-up that names only a bank relative to a retained balance is still a balance refinement,
  not an unsupported request or a lookup limited to the currently displayed row. Return that bank in filters.bank.
- For retained balance context, also return balance_delta. Scope operations: replace for newly targeted banks, add or
  remove for set edits, recent_two for the two most recently mentioned distinct banks, mentioned for every distinct
  bank mentioned in this balance thread, last_result for the last displayed set, all for every linked account,
  default for the default account, preserve when scope does not change.
- Balance operations: value for one account, total for one combined amount, breakdown for per-account values, and
  compare for an ordered comparison. Comparison and totals may contain any number of accounts, not only two.
- A reference to exactly two prior targets uses recent_two. References to the whole discussed set use mentioned;
  this set may contain more than two accounts. References to the displayed set use last_result. Resolve these only
  when the supplied balance context grounds the requested scope.
- Asking to reveal matching entries after a collection count/bool answer sets read_response_shape=surface_list and
  show_details.
- Asking for expanded information after a balance/default-account/ticket/receipt fact answer sets
  read_response_shape=surface_detail and show_details.
- Moving through a retained list sets page_action=next|previous|first; do not reinterpret it as a fresh request.
- For beneficiary, schedule, and linked-account frames, use set_scope_delta rather than copying records. Operations:
  preserve, replace, add, remove, recent_two, mentioned, last_result, all. Put only visible ordinals or display labels
  in the delta. Never invent IDs. Use recent_two/mentioned/last_result only when the frame conversation set grounds it.
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
