"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """## OUTPUT JSON
- Return only PlannerOutput JSON.
- greeting/thanks/check-in -> conversational with tasks=[].
- Banking asks -> task+ unless context_fastpath_subtype.
- transfer: send_money|schedule_transfer|recurring_transfer|list_scheduled_transfers|cancel_scheduled_transfer.
- beneficiary: list_beneficiaries|add_beneficiary|delete_beneficiary|update_beneficiary|save_beneficiary.
- query: transaction_list|transaction_search|analytics_summary|time_comparison|beneficiary_summary|affordability.
- beneficiary_route:
  beneficiary_list=list/manage, recipient_ranking=query.beneficiary_summary, none=save/other.
- save_beneficiary only with beneficiary suggestion context.
- recipient split -> recipient_allocations[].
- funding split -> explicit_split.
- account_action_hint: account asks incl. fastpath; else none."""

PLANNER_TRANSFER_PRECISION_PROMPT = """## MONEY_MOVE PRECISION
- Keep recipient exactly as typed; no context expansion.
- recipient_name must be plain text only.
- Selector refs: {"selector":"previous"} or {"selector":"index","index":N}.
- One-shot completeness: extract explicit amount/account/bank/phone/network/plan in same turn.
- Split across people/beneficiaries -> recipient_allocations, not explicit_split.
- Split across my funding accounts/banks -> explicit_split or source_accounts.
- Precision-first: never guess ambiguous fields."""

PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT = (
    "## EXECUTOR COVERAGE GUARD\n"
    "- expected_transaction_executors: {expected_executors}.\n"
    "- Explicit mixed asks must emit every expected executor in user order."
)

PLANNER_RULE_ATOMS: dict[str, str] = {
    "R01_CONVERSATIONAL": "greet|thanks|checkin->conversational,tasks=[]",
    "R02_BANKING_TASKS": "banking->task+",
    "R03_MISSING_SLOTS": "slots_missing->task+",
    "R04_DEPENDENCIES": "explicit_order->depends_on",
    "R05_CANCEL_CONFIRM": "cancel_word->is_cancellation; pure_approve->is_confirmation",
    "R06_AMOUNT_NORMALIZATION": "5k->5000",
    "R07_OUT_OF_SCOPE": "non_banking->conversational.out_of_scope",
    "R08_ACTION_EXECUTOR": "action==executor_family",
    "R09_CONTEXT_OVERRIDE": "active_flow_reply->slot_update unless switch/cancel",
    "R10_LANGUAGE_ALIGNMENT": "response_lang=detected_lang",
    "R11_RESPONSE_KEYS": "conversational_no_task->allowed response_key; cancel->planner.cancelled",
    "R12_BENEFICIARY_HANDLING": "save_beneficiary only_if explicit",
    "R13_QUERY_CONTINUATION": "active_query_followup->query; send_again|resend->transfer_replay",
    "R14_REFERENCE_BINDING": "pronoun|index->selector_ref",
    "R15_RESUMPTION": "resume_actions only_if explicit_resume_prompt",
    "R16_FASTPATH_CONTEXT_READ": "eligible_context_read may_return conversational tasks=[]",
    "R17_FASTPATH_FALLBACK": "context_missing|stale->worker_task",
    "R18_FASTPATH_SUBTYPE": "context_fastpath_subtype only_if eligible_context_read",
    "R19_TRANSFER_FIDELITY": "transfer_recipient_keep_exact_text",
    "R20_TRANSFER_ACCOUNT_BANK": "account+bank_together->recipient_account+bank_name",
    "R21_TRANSFER_SCHEDULING": (
        "future|repeat|list|cancel->schedule_transfer|recurring_transfer|"
        "list_scheduled_transfers|cancel_scheduled_transfer"
    ),
    "R22_MIXED_MONEY_MOVE": "explicit transfer+airtime+data mix->emit all tasks in order",
    "R23_MULTILINGUAL_SAFETY": "rules_apply_semantically_across_supported_languages",
    "R24_BENEFICIARY_ROUTE": "saved_beneficiaries->beneficiary_list; top_recipients->recipient_ranking",
    "R25_ACCOUNT_ACTION_HINT": "account_asks->account_action_hint even_when tasks=[]",
    "R26_ONE_SHOT_COMPLETENESS": "one_shot_tx->extract all explicit fields without correction dependence",
    "R27_RECIPIENT_SPLIT": "split_people->recipient_allocations; split_my_accounts->explicit_split",
}

PLANNER_RULE_ATOM_ORDER = [
    "R01_CONVERSATIONAL",
    "R02_BANKING_TASKS",
    "R03_MISSING_SLOTS",
    "R04_DEPENDENCIES",
    "R05_CANCEL_CONFIRM",
    "R06_AMOUNT_NORMALIZATION",
    "R07_OUT_OF_SCOPE",
    "R08_ACTION_EXECUTOR",
    "R09_CONTEXT_OVERRIDE",
    "R10_LANGUAGE_ALIGNMENT",
    "R11_RESPONSE_KEYS",
    "R12_BENEFICIARY_HANDLING",
    "R13_QUERY_CONTINUATION",
    "R14_REFERENCE_BINDING",
    "R15_RESUMPTION",
    "R16_FASTPATH_CONTEXT_READ",
    "R17_FASTPATH_FALLBACK",
    "R18_FASTPATH_SUBTYPE",
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R21_TRANSFER_SCHEDULING",
    "R22_MIXED_MONEY_MOVE",
    "R23_MULTILINGUAL_SAFETY",
    "R24_BENEFICIARY_ROUTE",
    "R25_ACCOUNT_ACTION_HINT",
    "R26_ONE_SHOT_COMPLETENESS",
    "R27_RECIPIENT_SPLIT",
]

# Critical rules whose semantics must be explicit in the compiled prompt to prevent drift.
PLANNER_RULE_SEMANTIC_GUARD_IDS = {
    "R05_CANCEL_CONFIRM",
    "R14_REFERENCE_BINDING",
    "R17_FASTPATH_FALLBACK",
}

PLANNER_BASE_RULE_ATOMS = {
    "R01_CONVERSATIONAL",
    "R02_BANKING_TASKS",
    "R03_MISSING_SLOTS",
    "R04_DEPENDENCIES",
    "R05_CANCEL_CONFIRM",
    "R06_AMOUNT_NORMALIZATION",
    "R07_OUT_OF_SCOPE",
    "R08_ACTION_EXECUTOR",
    "R10_LANGUAGE_ALIGNMENT",
    "R11_RESPONSE_KEYS",
    "R16_FASTPATH_CONTEXT_READ",
    "R17_FASTPATH_FALLBACK",
    "R18_FASTPATH_SUBTYPE",
    "R23_MULTILINGUAL_SAFETY",
    "R24_BENEFICIARY_ROUTE",
    "R25_ACCOUNT_ACTION_HINT",
}

PLANNER_MONEY_MOVE_RULE_ATOMS = {
    "R09_CONTEXT_OVERRIDE",
    "R14_REFERENCE_BINDING",
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R21_TRANSFER_SCHEDULING",
    "R22_MIXED_MONEY_MOVE",
    "R26_ONE_SHOT_COMPLETENESS",
    "R27_RECIPIENT_SPLIT",
}
PLANNER_QUERY_RULE_ATOMS = {"R13_QUERY_CONTINUATION", "R14_REFERENCE_BINDING"}
PLANNER_CONTEXT_RULE_ATOMS = {
    "R09_CONTEXT_OVERRIDE",
    "R12_BENEFICIARY_HANDLING",
    "R14_REFERENCE_BINDING",
    "R15_RESUMPTION",
}

PLANNER_RUNTIME_COMMON_EXAMPLES = """## TARGETED EXAMPLES (COMMON)
- Send 8k -> send_money amount=8000, recipient omitted."""

PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES = """## TARGETED EXAMPLES (MONEY_MOVE)
- Send 10k to Mum and buy 5k airtime -> send_money + buy_airtime.
- Buy 5k airtime then send 10k to Mum -> use depends_on.
- Send 20k to 0760505261 First Bank -> send_money amount=20000, recipient_account=0760505261, bank_name=First Bank.
- Split 20k between Mum and Gaines ->
  send_money amount=20000,
  recipient_allocations=[{recipient_name:Mum,amount:10000},{recipient_name:Gaines,amount:10000}].
- Send 20k 70/30 btw Mum and Gaines ->
  send_money amount=20000,
  recipient_allocations=[{recipient_name:Mum,amount:14000},{recipient_name:Gaines,amount:6000}].
- Split 20k from Access and GTB -> send_money amount=20000, explicit_split={Access:10000,GTB:10000}.
- Abeg buy 2k airtime for 08031234567 mtn -> buy_airtime amount=2000, recipient_phone=08031234567, network=MTN.
- Jowo ra data 1gb fun 08031234567 mtn -> buy_data plan=1GB, recipient_phone=08031234567, network=MTN.
- Don Allah tura 5k zuwa 0760505261 First Bank ->
  send_money amount=5000, recipient_account=0760505261, bank_name=First Bank.
- Biko buy 3k airtime for my line mtn -> buy_airtime amount=3000, is_self=true, network=MTN.
- Envoie 5k a 0760505261 First Bank -> send_money amount=5000, recipient_account=0760505261, bank_name=First Bank."""

PLANNER_RUNTIME_QUERY_EXAMPLES = """## TARGETED EXAMPLES (QUERY)
- Active Query Session + "any credits?" -> transaction_search.
- Active Query Session + "send again"/"resend" -> send_money."""

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Fallback only: router handles short grounded read-only follow-ups first.
- Save-beneficiary prompt + "Hi" -> conversational.
- Recent Chat account_count + "List them" -> linked_accounts_summary.
- Recent Chat beneficiary_count + "List them" -> beneficiary_list."""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return schema JSON"

PROMPT_BUNDLE_ORDER = (
    "money_move",
    "query",
    "context",
    "executor_coverage_guard",
)
