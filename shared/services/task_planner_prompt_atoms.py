"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """## OUTPUT JSON
- Return only PlannerOutput JSON.
- greeting/thanks/check-in -> conversational with tasks=[].
- Banking asks handled by planner should emit task+.
- transfer: send_money|schedule_transfer|recurring_transfer|list_scheduled_transfers|cancel_scheduled_transfer.
- beneficiary: list_beneficiaries|add_beneficiary|delete_beneficiary|update_beneficiary|save_beneficiary.
- query: transaction_list|transaction_search|analytics_summary|time_comparison|beneficiary_summary|affordability.
- beneficiary_route:
  beneficiary_list=list/manage, recipient_ranking=query.beneficiary_summary, none=save/other.
- save_beneficiary only with beneficiary suggestion context.
- people transfer batch -> one task + recipient_allocations[].
- funding split -> explicit_split.
- Mixed/orchestration asks may include multiple tasks with depends_on preserving user order."""

PLANNER_TRANSFER_PRECISION_PROMPT = """## MONEY_MOVE PRECISION
- Keep recipient exactly as typed.
- recipient_name must be plain text.
- Selector refs: {"selector":"previous"} or {"selector":"index","index":N}.
- Extract explicit amount/account/bank/phone/network/plan in one turn.
- People split -> one transfer task + recipient_allocations, never explicit_split.
- "each" + named recipient list -> one allocation per recipient, in order.
- Funding-account split -> explicit_split or source_accounts.
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
    "R14_REFERENCE_BINDING": "pronoun|index->selector_ref",
    "R15_RESUMPTION": "resume_actions only_if explicit_resume_prompt",
    "R19_TRANSFER_FIDELITY": "transfer_recipient_keep_exact_text",
    "R20_TRANSFER_ACCOUNT_BANK": "account+bank_together->recipient_account+bank_name",
    "R21_TRANSFER_SCHEDULING": (
        "future|repeat|list|cancel->schedule_transfer|recurring_transfer|"
        "list_scheduled_transfers|cancel_scheduled_transfer"
    ),
    "R22_MIXED_MONEY_MOVE": (
        "mix transfer+airtime+data->emit tasks in order;"
        " people batch->one transfer task+recipient_allocations;"
        " multiple airtime/data targets->one task per target"
    ),
    "R23_MULTILINGUAL_SAFETY": "rules_apply_semantically_across_supported_languages",
    "R24_BENEFICIARY_ROUTE": "mixed asks may use beneficiary_list or recipient_ranking hints when helpful",
    "R25_ACCOUNT_ACTION_HINT": "mixed account asks may set account_action_hint when helpful",
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
    "R14_REFERENCE_BINDING",
    "R15_RESUMPTION",
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
PLANNER_TRANSFER_ONLY_RULE_ATOMS = {
    "R09_CONTEXT_OVERRIDE",
    "R14_REFERENCE_BINDING",
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R21_TRANSFER_SCHEDULING",
    "R26_ONE_SHOT_COMPLETENESS",
    "R27_RECIPIENT_SPLIT",
}
PLANNER_CONTEXT_RULE_ATOMS = {
    "R09_CONTEXT_OVERRIDE",
    "R12_BENEFICIARY_HANDLING",
    "R14_REFERENCE_BINDING",
    "R15_RESUMPTION",
}

PLANNER_RUNTIME_COMMON_EXAMPLES = """## TARGETED EXAMPLES (COMMON)
- Send 8k -> send_money amount=8000, recipient omitted."""

PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES = (
    "## TARGETED EXAMPLES (MONEY_MOVE)\n"
    "- Send 10k to Mum and buy 5k airtime -> send_money + buy_airtime.\n"
    "- Send 20k to 0760505261 First Bank -> send_money amount=20000, "
    "recipient_account=0760505261, bank_name=First Bank.\n"
    "- Split 20k between Mum and Gaines -> recipient_allocations="
    "[{recipient_name:Mum,amount:10000},{recipient_name:Gaines,amount:10000}].\n"
    "- Send 10k each to Mum, Tolu and Doyin -> recipient_allocations="
    "[{recipient_name:Mum,amount:10000},{recipient_name:Tolu,amount:10000},{recipient_name:Doyin,amount:10000}].\n"
    "- Send 20k 70/30 btw Mum and Gaines -> recipient_allocations="
    "[{recipient_name:Mum,amount:14000},{recipient_name:Gaines,amount:6000}].\n"
    "- Split 20k from Access and GTB -> send_money amount=20000, explicit_split={Access:10000,GTB:10000}.\n"
    "- Abeg buy 2k airtime for 08031234567 mtn -> buy_airtime amount=2000, recipient_phone=08031234567, network=MTN.\n"
    "- Jowo ra data 1gb fun 08031234567 mtn -> buy_data plan=1GB, recipient_phone=08031234567, network=MTN.\n"
    "- Don Allah tura 5k zuwa 0760505261 First Bank ->\n"
    "  send_money amount=5000, recipient_account=0760505261, bank_name=First Bank.\n"
    "- Biko buy 3k airtime for my line mtn -> buy_airtime amount=3000, is_self=true, network=MTN.\n"
    "- Buy 200 airtime for 08031234567, 08067892221, 08033038674 ->\n"
    "  3 x buy_airtime: each amount=200, recipient_phone per number.\n"
    "- Envoie 5k a 0760505261 First Bank -> send_money amount=5000, recipient_account=0760505261, bank_name=First Bank."
)

PLANNER_RUNTIME_TRANSFER_ONLY_EXAMPLES = (
    "## TARGETED EXAMPLES (TRANSFER_ONLY)\n"
    "- Send 20k to 0760505261 First Bank -> send_money amount=20000, "
    "recipient_account=0760505261, bank_name=First Bank.\n"
    "- Send 10k each to Mum, Tolu and Doyin -> recipient_allocations="
    "[{recipient_name:Mum,amount:10000},{recipient_name:Tolu,amount:10000},{recipient_name:Doyin,amount:10000}].\n"
    "- Split 20k 70/30 btw Mum and Gaines -> recipient_allocations="
    "[{recipient_name:Mum,amount:14000},{recipient_name:Gaines,amount:6000}].\n"
    "- Send half my Zenith to Mum -> send_money recipient_name=Mum, source_bank_name=Zenith Bank, "
    "transfer_percentage=50.\n"
    "- Send everything in my First Bank to Mum -> send_money recipient_name=Mum, source_bank_name=First Bank, "
    "transfer_all=true."
)

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Save-beneficiary prompt + "Hi" -> conversational.
- Active transfer flow + "send it to her" -> send_money with selector reference.
- Resume prompt + "continue" -> stay on current transactional flow."""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return schema JSON"

PROMPT_BUNDLE_ORDER = (
    "transfer_only",
    "money_move",
    "context",
    "executor_coverage_guard",
)
