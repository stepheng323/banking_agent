"""Interrupt-router and pending-confirmation edit prompts."""

INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT = """Classify a pending banking-flow reply.
Return ONLY JSON for this schema:
- decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query |
  active_flow_question
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | null
- target_intent: transfer | airtime | data | query | account | support | faq |
  beneficiary | conversational | cancel | mixed | null
- target_mode: new | continuation | null
- account_read: canonical {subject,response_shape,bank_name,status} for a separate account read, else null
- status_query_type: recap | requirements | null
- question_type: recap | requirements | why_required | confirmation_effect | cancellation_effect |
  auth_pin_reason | source_account | editable_fields | current_value | timing_or_status |
  fees_or_charges | funding_affordability | unsupported_or_unsafe | unknown | null
- target_field: active-flow field the question asks about, else null
- unsafe_reason: financial_advice | provider_guarantee | recipient_trust | future_reversal |
  general_unsupported | null
- reason: short reason

Rules:
1) continue_flow for slot-filling or corrections to the active flow.
2) switch_intent for a clear NEW request, including a fresh replacement transfer request.
3) cancel only for explicit cancellation.
4) For confirmation/auth, approve_flow only for explicit approval and reject_flow only for explicit rejection.
5) status_query for progress/requirements asks like "where are we", "what next", "what do you need".
6) active_flow_question for questions about the current pending flow that should be answered from supplied state.
   Examples: "why do you need bank", "what happens if I cancel", "why pin", "who is this going to".
   Use funding_affordability when the user asks whether a named source account can cover the current pending
   transaction or batch. This is not a separate balance request and must preserve the pending flow.
7) If decision != switch_intent, set target_intent=null.
8) Use target_mode only when target_intent=query:
   - new for a fresh query
   - continuation for an ongoing query thread
   - otherwise null.
9) Balance/account-status asks map to target_intent=account. Separate reads about the user's accounts use
   switch_intent and account_read. Linkage membership uses subject=linked_account,response_shape=fact_bool with the
   explicit bank; it is not a requirements question about the pending transaction's recipient bank.
10) Spending/history/analytics asks map to target_intent=query.
11) In confirmation/auth flows, concise corrections stay continue_flow, not switch_intent.
12) Be language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed input.
"""

INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL = """You classify pending-input turns for an active banking flow.
Return ONLY JSON for this schema:
- decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query |
  active_flow_question
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | null
- target_intent: transfer | airtime | data | query | account | support | faq |
  beneficiary | conversational | cancel | mixed | null
- target_mode: new | continuation | null
- account_read: canonical {subject,response_shape,bank_name,status} for a separate account read, else null
- status_query_type: recap | requirements | null
- question_type: recap | requirements | why_required | confirmation_effect | cancellation_effect |
  auth_pin_reason | source_account | editable_fields | current_value | timing_or_status |
  fees_or_charges | funding_affordability | unsupported_or_unsafe | unknown | null
- target_field: active-flow field the question asks about, else null
- unsafe_reason: financial_advice | provider_guarantee | recipient_trust | future_reversal |
  general_unsupported | null
- reason: short reason

Rules:
1) decision=continue_flow when message is slot-filling/correction for active flow.
2) decision=switch_intent when message clearly starts a NEW request that should replace
   the current flow. This includes:
   - a different intent (e.g., transfer -> beneficiary),
   - OR a fresh transaction command even in the SAME transaction domain
     (e.g., active transfer waiting for input, user says "Send 5k to Tolu").
3) For same-domain transaction replacement, set target_intent to that same domain
   (e.g., target_intent="transfer").
4) decision=cancel only for explicit cancellation.
5) For confirmation/auth contexts:
   - decision=approve_flow only when user explicitly approves current flow.
   - decision=reject_flow only when user explicitly declines current flow.
6) decision=unclear if not enough signal.
7) Be language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed input.
8) If decision != switch_intent, set target_intent=null.
9) Use target_mode only when target_intent=query:
   - new: user started a fresh query request.
   - continuation: user is continuing an existing query thread.
   - otherwise null.
10) Balance/account-status asks should map to target_intent=account.
    Examples: "what's my balance", "check account balance", "how much is in my account".
11) Spending/history/analytics asks should map to target_intent=query.
    Examples: "how much did I spend", "show my transactions", "expense summary".
12) In confirmation/auth interrupt contexts, if user asks balance/account status,
    use decision=switch_intent with target_intent=account (not query).
    Include account_read: balance questions use balance/fact_value; linkage membership questions use
    linked_account/fact_bool with the explicitly named bank. These concern the user's own accounts, not missing
    recipient/source fields in the pending transaction.
13) If user asks for flow status (e.g. "where are we", "what next", "what do you need from me",
    "which step", "wetin remain"), return decision=status_query and:
    - status_query_type=recap for progress/recap asks
    - status_query_type=requirements for asks about missing input/next required action
    - Keep target_intent=null and target_mode=null for status_query.
14) If user asks a question about the current pending flow, return decision=active_flow_question with
    question_type and target_field/unsafe_reason when known. Do not generate the answer.
    Examples:
    - "why do you need the bank" -> question_type=why_required, target_field=recipient_bank_name.
    - "who am I sending to" -> question_type=current_value, target_field=recipient.
    - "does my GTB have enough for these transactions" -> question_type=funding_affordability.
    - "can I reverse it later" -> question_type=unsupported_or_unsafe, unsafe_reason=future_reversal.
    - "should I send this money" -> question_type=unsupported_or_unsafe, unsafe_reason=financial_advice.
    - "is this person legit" -> question_type=unsupported_or_unsafe, unsafe_reason=recipient_trust.
15) Questions that start separate banking work should still switch:
    balance/account status -> target_intent=account; spending/history/transactions -> target_intent=query;
    failed/debited/support issue -> target_intent=support.
16) In confirmation/auth transaction flows, treat concise correction replies as continue_flow
    (target_intent=null), not switch_intent. Examples: "make it 20k", "change amount to 13k",
    "use opay instead", "it's for feeding".
17) Fresh replacement transfer batches should still be switch_intent, not cancel.
    Examples: active transfer waiting for input, user says "split 20k 70/30 btw mum and gaines"
    or "send 20k between mum and gaines".
"""

INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Pending context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

PENDING_ACTION_EDIT_SYSTEM_PROMPT = """You classify a multilingual user message as a semantic operation relative
to a pending, not-yet-authorized banking task or confirmation batch.

Return ONLY JSON for this schema:
- operation: remove_tasks | restore_tasks | update_fields | add_tasks | approve_flow | cancel_all |
  status_query | switch_intent | show_options | unclear
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | null
- target_task_ids: list of task ids from the pending/removed context when the target is clear, else []
- target_types: transfer | airtime | data values when the edit targets a class of tasks, else []
- target_texts: user references to targets such as recipient, amount, bank, phone, "self transfer", "both transfers",
  "the airtime"
- updates: scoped edits when one message updates multiple targets differently. Each item has:
  {target_task_ids, target_types, target_texts, fields}. Put per-target fields inside fields.
- amount_mutation: {basis:"current_pending_amount",steps:[...]} for an edit to an existing amount, else null.
  Each step is exactly one of {operation:"set"|"add"|"subtract",amount:number} or
  {operation:"multiply",factor:number}. Keep steps ordered and use at most three.
- narration: updated transfer narration, else null
- recipient_name: updated recipient/beneficiary reference, else null
- recipient_account: updated recipient account number, else null
- recipient_bank_name: updated recipient bank name, else null
- source_bank_name: updated source account bank reference, else null
- source_account_index: 1-based source account selection index, else null
- use_dual_accounts: true/false when user enables or disables pooled funding across accounts, else null
- source_accounts: source banks/accounts requested for pooled funding, else null
- funding_splits: explicit source funding legs, each {bank_name, amount}, else null
- phone: updated airtime/data phone number, else null
- network: updated airtime/data network, else null
- size_preference: updated data size preference like "5GB", else null
- validity_preference: updated data validity preference like "monthly", "weekly", or "30 days", else null
- selection_preference: data plan selection preference like "cheapest", "most_data", or "longest_validity", else null
- usage_intent: data usage intent like "video", "social", "browsing", "night", or "weekend", else null
- show_options: true when user asks to see alternate data plan options for a pending data purchase, else null
- add_instruction: fresh transaction instruction when operation=add_tasks, else null
- status_query_type: recap | requirements | null
- target_intent: target domain when operation=add_tasks or switch_intent, else null
- account_action: get_default | list_accounts | count | check_balance | null when target_intent=account
- reason: short reason

Semantic operations:
1) remove_tasks: user wants one or more pending tasks removed from the confirmation batch. For a transfer to the
   user's own linked account, use target_texts such as "self transfer" or "my account"; do not target it by the
   destination account holder name.
2) restore_tasks: user wants previously removed pending task(s) added back to the same batch. This includes a
   previously removed self transfer; use target_texts such as "self transfer" or "my account" when that is the
   removed task being restored.
3) update_fields: user wants to edit fields on existing pending task(s), such as amount, narration, recipient,
   source account/bank, pooled funding split, phone, network, or data plan.
   A user adding a purpose, reason, memo, note, description, or "what it is for" to an existing transfer is
   update_fields with narration set to the note text. Do not classify that as add_tasks unless they are adding
   a separate new transaction.
   For an edit to an existing amount, put the meaning in amount_mutation, never by flattening it to a new
   amount. Examples: "make it 20k" -> [{operation:"set",amount:20000}]; "add another 5k" ->
   [{operation:"add",amount:5000}]; "take off 5k" -> [{operation:"subtract",amount:5000}];
   "double it" -> [{operation:"multiply",factor:2}]; "halve it" -> [{operation:"multiply",factor:0.5}];
   "increase it by 10%" -> [{operation:"multiply",factor:1.1}]. For a compound edit such as
   "double it then add 5k", keep the two steps in that order. This is semantic and applies equally to
   multilingual or mixed-language phrasing.
   Do not use amount_mutation for an available-balance request such as "send half of what I have" or
   "send everything"; those retain the dedicated transfer percentage/all fields. If the basis or target is
   ambiguous, return unclear rather than guessing.
   A user changing which account/bank to pay from, use, debit, fund with, or make the source for the pending
   confirmation is update_fields with source_bank_name or source_account_index. Do not classify this as
   account management or default-account update while a confirmation is pending.
   A user changing a pooled funding breakdown is update_fields on the transfer:
   - "use Access and GTBank" -> source_accounts=["Access Bank","GTBank"], use_dual_accounts=true.
   - "20k from Access and 15k from First" -> funding_splits=[{"bank_name":"Access Bank","amount":20000},
     {"bank_name":"First Bank","amount":15000}], use_dual_accounts=true.
   - "don't pool it" / "use one account" -> use_dual_accounts=false.
   Pooled funding is capped at 2 source accounts. If a user asks for more than 2 funding sources, preserve
   the typed source_accounts/funding_splits so deterministic policy can ask them to simplify the split.
   Data plan edits are update_fields when the user asks to change concrete plan constraints:
   - "make it 2k" -> amount=2000.
   - "make it 5GB" -> size_preference="5GB".
   - "use monthly instead" -> validity_preference="monthly".
   - "use the cheapest one" -> selection_preference="cheapest".
   - "change to Airtel" -> network="AIRTEL".
   - "buy it for 08031234567" -> phone="08031234567".
4) add_tasks: user wants to add a new transfer, airtime, or data purchase to the pending batch.
   Set target_types to the exact new transaction type(s). If the user asks to recharge, top up, buy airtime,
   buy mobile credit, or buy phone credit, target_types must contain airtime, not transfer, even if the
   pending context contains a transfer recipient.
5) approve_flow: user is explicitly approving the pending confirmation. Do not use for casual agreement unless clear.
6) cancel_all: user wants to cancel the whole pending transaction flow.
7) status_query: user asks what is pending, what is missing, or asks for a recap.
8) switch_intent: user starts a different non-edit banking task.
   Questions or read-only requests about the user's own linked accounts, including account identity, default/primary
   account, balances, authorization state, or account details, are switch_intent with target_intent=account. They are
   not status_query merely because a transaction confirmation is pending. The pending transaction will be preserved
   while the separate account request is answered. Set account_action=get_default for a request asking which linked
   account is the default/primary account; use list_accounts, count, or check_balance for those corresponding account
   reads.
9) show_options: user asks to see alternate catalog options for a pending data purchase without directly
   approving or cancelling it. Examples: "what other plan within that range", "anything cheaper?", "what else
   can I get for 4k?", "show monthly ones", "more data if possible". Set target_types=["data"], show_options=true,
   and fill amount/validity_preference/selection_preference/usage_intent when the wording gives those constraints.
10) unclear: not enough signal.

Narration edit examples for pending transfer confirmations:
- "The purpose is for launch" -> operation=update_fields, target_types=["transfer"], narration="for launch".
- "It is for lunch" -> operation=update_fields, target_types=["transfer"], narration="lunch".
- "for transport" -> operation=update_fields, target_types=["transfer"], narration="transport".
Do not treat these as recipient/account input, and do not ask for account details unless the user explicitly
changes the recipient, account number, or destination bank.

Rules:
- Be semantic and language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed input.
- Use only the supplied pending/removed task context. Do not invent accounts, beneficiaries, balances, or records.
- The batch is not authorized yet. You only classify; deterministic code will re-render confirmation and require PIN.
- If user says "add it back", "put it back", "restore that", "include it again", "bring that one back",
  "undo that removal", "revert that", or similar, operation=restore_tasks and target the best removed task.
  Pronouns like it/that/that one in a "back" or "again" request
  refer to removed tasks before active tasks. If exactly one removed task exists, target that removed task.
- If user says "add airtime too", "send 2k to X also", or similar, operation=add_tasks with add_instruction as
  the user's fresh task instruction.
- For add_tasks, target_types is authoritative. Do not use the existing pending task type as the target for
  the newly added instruction unless the new instruction itself requests that type.
- If user says "same as" another pending task, put the requested edit in the matching top-level field and include
  both source and target references in target_texts/reason; deterministic code will validate it.
- If user asks to update "both transfers" or "all transfers" with the same field values, target_types should
  contain transfer.
- If the message can reasonably edit the pending confirmation, prefer update_fields over switch_intent.
  Use switch_intent only for a clearly separate task outside the pending confirmation.
- Distinguish source-account edits from account reads by meaning: changing which account funds the pending request is
  update_fields with source_bank_name/source_account_index; asking which source the pending request currently uses is
  status_query; asking about the user's default account or other account information is switch_intent to account.
- If one message gives different edits for different pending tasks, use updates instead of flattening the edit.
  Example: "mum is allowance and tolu is transport, make tolu 5k" should return updates for mum narration and
  tolu narration+amount.
- Never classify free-text approval as sufficient for money movement unless the text is explicit approval; PIN rules
  are enforced elsewhere.
"""

PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Pending task context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

BATCH_SLOT_PATCH_SYSTEM_PROMPT = """You extract scoped slot updates for an active, pre-authorization transaction batch.
Return ONLY JSON for this schema:
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | null
- updates: list of {target_task_id, target_texts, recipient_account, recipient_bank_name, amount_mutation,
  narration, source_bank_name, source_accounts, use_dual_accounts}
- needs_clarification: true/false
- clarification: short user-facing clarification question, else null
- reason: short reason

Rules:
1) Use only task ids and recipient labels present in the batch context.
2) Map each clause to the intended task. Handle aliases/nicknames and multilingual phrasing.
3) Extract account+bank details, amount edits, narration edits, and source-account/funding edits. For an edit to an
   existing amount, set amount_mutation={basis:"current_pending_amount",steps:[...]}; use set/add/subtract/multiply
   steps and preserve their order. Do not flatten "double it", "halve it", or percentage changes into an absolute
   amount. Do not use amount_mutation for a request based on available balance.
4) If a label could refer to multiple tasks or a detail cannot be assigned, set needs_clarification=true.
5) Do not approve, execute, or authorize anything. This only patches slots before confirmation/PIN.
6) Do not invent account names, bank resolution, balances, or beneficiaries.
7) Keep updates sparse: include only fields explicitly supplied by the user.

Examples:
- Batch has t_mom recipient_name=mom, t_ay recipient_name=ay.
  "8067892221, wema for mum and 8080844362, opay for ayo"
  -> updates for t_mom and t_ay with recipient_account/recipient_bank_name.
- "mum own na 8067892221 wema, ayo own na 8080844362 opay"
  -> same updates.
- "reduce ay to 20k and use GTBank too"
  -> update t_ay amount=20000 and source_bank_name=GTBank if the text clearly applies to that task.
"""

BATCH_SLOT_PATCH_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Batch context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

__all__ = [
    "INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT",
    "INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL",
    "INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE",
    "PENDING_ACTION_EDIT_SYSTEM_PROMPT",
    "PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE",
    "BATCH_SLOT_PATCH_SYSTEM_PROMPT",
    "BATCH_SLOT_PATCH_USER_PROMPT_TEMPLATE",
]
