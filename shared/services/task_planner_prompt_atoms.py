"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """## OUTPUT JSON
- Return only PlannerOutput JSON.
- greeting/thanks/check-in -> conversational with tasks=[].
- Banking asks emit >=1 task unless using context_fastpath_subtype.
- action must be concrete and executor-matched.
- transfer: send_money|schedule_transfer|recurring_transfer|list_scheduled_transfers|cancel_scheduled_transfer.
- query: transaction_list|transaction_search|analytics_summary|time_comparison|beneficiary_summary|affordability.
- beneficiary_route:
  beneficiary_list for saved-beneficiary list/manage; recipient_ranking for top recipients; else none.
- account_action_hint: set intended account action for account asks (including fastpath tasks=[]), else none."""

PLANNER_TRANSFER_PRECISION_PROMPT = """## MONEY_MOVE PRECISION
- Keep recipient exactly as typed; no context expansion.
- recipient_name must be plain text only.
- Selector refs: {"selector":"previous"} or {"selector":"index","index":N}."""

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
}
PLANNER_QUERY_RULE_ATOMS = {"R13_QUERY_CONTINUATION", "R14_REFERENCE_BINDING"}
PLANNER_CONTEXT_RULE_ATOMS = {
    "R09_CONTEXT_OVERRIDE",
    "R12_BENEFICIARY_HANDLING",
    "R14_REFERENCE_BINDING",
    "R15_RESUMPTION",
}

PLANNER_RUNTIME_COMMON_EXAMPLES = """## TARGETED EXAMPLES (COMMON)
- How far -> conversational.checkin.
- Send 8k -> send_money amount=8000 (recipient omitted).
- Buy 1k airtime -> buy_airtime amount=1000."""

PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES = """## TARGETED EXAMPLES (MONEY_MOVE)
- Send 10k to Mum and buy 5k airtime -> send_money + buy_airtime.
- Buy 5k airtime then send 10k to Mum -> use depends_on."""

PLANNER_RUNTIME_QUERY_EXAMPLES = """## TARGETED EXAMPLES (QUERY)
- Active Query Session + "any credits?" -> transaction_search.
- Active Query Session + "send again"/"resend" -> send_money."""

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Asked to save beneficiary + "Hi" -> conversational.
- Recent Chat account_count + "List them" -> context_fastpath_subtype=linked_accounts_summary.
- Recent Chat beneficiary_count + "List them" -> context_fastpath_subtype=beneficiary_list."""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return schema JSON"

PROMPT_BUNDLE_ORDER = (
    "money_move",
    "query",
    "context",
    "executor_coverage_guard",
)
