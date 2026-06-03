"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """## OUTPUT JSON
- PlannerOutput JSON only.
- greet|thanks|checkin -> conversational,tasks=[]; banking/missing -> task+.
- mixed asks: clauses[] before tasks[]; tasks may set source_clause_index.
- transfer: send_money|schedule_transfer|recurring_transfer.
- schedule executor: list/find/cancel/edit_scheduled_transaction(s); count/existence->schedule_response_mode=count.
- aliases: list_scheduled_transfers|cancel_scheduled_transfer.
- airtime: buy_airtime|schedule_airtime|recurring_airtime; data: buy_data|schedule_data|recurring_data.
- beneficiary: list/add/delete/update/save; query: list/search/analytics/time/beneficiary/affordability.
- beneficiary_route: beneficiary_list|recipient_ranking|none; people batch->recipient_allocations[].
- funding split->explicit_split; mixed->depends_on."""

PLANNER_TRANSFER_PRECISION_PROMPT = """## MONEY_MOVE PRECISION
- Keep recipient exactly as typed.
- Selector refs: {"selector":"previous"} or {"selector":"index","index":N}.
- Preserve transactional corrections and follow-up slot updates.
- Preserve scheduling semantics for future/repeating transfer, airtime, and data requests.
- Keep explicit_split only for source-account funding splits, never recipient names.
- Precision-first: never guess ambiguous fields."""

PLANNER_TRANSFER_ONLY_PRECISION_PROMPT = """## TRANSFER_ONLY PRECISION
- Keep recipient text exact.
- Extract explicit amount/account/bank/source-bank in one turn.
- People split -> recipient_allocations.
- Balance-share transfer -> transfer_percentage or transfer_all.
- Never guess ambiguous fields."""

PLANNER_MIXED_TX_PRECISION_PROMPT = """## MIXED_TX PRECISION
- Decompose mixed turns into ordered semantic clauses before tasks.
- Apply clause decomposition semantically across supported languages.
- Emit every explicit transaction executor in user order.
- Keep read-only balance/query clauses separate from transaction clauses.
- Keep transfer recipient text exact.
- Extract explicit transfer/account/bank and airtime/data phone/network fields in one turn.
- Preserve recipient_allocations for people splits.
- Never use text from one clause to fill another clause's slots.
- Never drop a later read-only clause.
- Never guess ambiguous fields."""

PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT = (
    "## EXECUTOR COVERAGE GUARD\n"
    "- expected_transaction_executors: {expected_executors}.\n"
    "- Emit every expected executor in user order."
)

PLANNER_RULE_ATOMS: dict[str, str] = {
    "R01_CONVERSATIONAL": "greet(howfar)|thanks|checkin(youdey)->conversational,tasks=[]",
    "R02_BANKING_TASKS": "banking->task+",
    "R03_MISSING_SLOTS": "slots_missing->task+",
    "R04_DEPENDENCIES": "explicit_order->depends_on",
    "R05_CANCEL_CONFIRM": "cancel_word->is_cancellation; pure_approve->is_confirmation",
    "R06_AMOUNT_NORMALIZATION": "5k->5000",
    "R07_OUT_OF_SCOPE": (
        "harmless_non_banking->conversational.casual_chat; unsupported_non_banking->conversational.out_of_scope"
    ),
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
        "future|repeat transfer->schedule_transfer|recurring_transfer;"
        "future|repeat airtime->schedule_airtime|recurring_airtime;"
        "future|repeat data->schedule_data|recurring_data;"
        "list|count|find|cancel|delete|edit scheduled->"
        "list_scheduled_transactions|find_scheduled_transaction|cancel_scheduled_transaction|edit_scheduled_transaction;"
        "count|existence scheduled->schedule_response_mode=count"
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
}
PLANNER_TRANSFER_ONLY_RULE_ATOMS = {
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R26_ONE_SHOT_COMPLETENESS",
    "R27_RECIPIENT_SPLIT",
}
PLANNER_MIXED_TX_RULE_ATOMS = {
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R22_MIXED_MONEY_MOVE",
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
    '- Active transfer flow + "send it to her" -> send_money with selector={"selector":"previous"}.\n'
    '- Active transfer flow + "make it 20k" -> send_money amount=20000.\n'
    "- Send 10k to Mum tomorrow 9am -> schedule_transfer amount=10000, recipient_name=Mum.\n"
    '- Send it to her every Friday -> recurring_transfer with selector={"selector":"previous"}.\n'
    "- Buy 2k airtime tomorrow 8am -> schedule_airtime amount=2000.\n"
    "- Buy 1GB data every Friday 8am -> recurring_data.\n"
    "- Split 20k from Access and GTB -> send_money amount=20000, explicit_split={Access:10000,GTB:10000}.\n"
    "- Biko buy 3k airtime for my line mtn -> buy_airtime amount=3000, is_self=true, network=MTN."
)

PLANNER_RUNTIME_TRANSFER_ONLY_EXAMPLES = (
    "## TARGETED EXAMPLES (TRANSFER_ONLY)\n"
    "- Send 20k to 0760505261 First Bank -> amount=20000, recipient_account=0760505261, bank_name=First Bank.\n"
    "- Send 10k each to Mum, Tolu and Doyin -> recipient_allocations="
    "[{recipient_name:Mum,amount:10000},{recipient_name:Tolu,amount:10000},"
    "{recipient_name:Doyin,amount:10000}].\n"
    "- Split 20k 70/30 btw Mum and Gaines -> recipient_allocations="
    "[{recipient_name:Mum,amount:14000},{recipient_name:Gaines,amount:6000}].\n"
    "- Send half my Zenith to Mum -> recipient_name=Mum, source_bank_name=Zenith Bank, transfer_percentage=50.\n"
    "- Send everything in my First Bank to Mum -> recipient_name=Mum, source_bank_name=First Bank, transfer_all=true."
)

PLANNER_RUNTIME_MIXED_TX_EXAMPLES = (
    "## TARGETED EXAMPLES (MIXED_TX)\n"
    "- Send 10k to Mum and buy 5k airtime -> send_money + buy_airtime.\n"
    "- Send 4k to Gaines, but 2k airtime for 08162511024 and show my final balance ->\n"
    "  clauses=[transfer, airtime, account_query] + send_money + buy_airtime + check_balance.\n"
    "- Send 10k each to Mum and Tolu, then buy 2k airtime for me ->\n"
    "  send_money recipient_allocations=[{recipient_name:Mum,amount:10000},"
    "{recipient_name:Tolu,amount:10000}] + buy_airtime.\n"
    "- Buy 1GB for 08031234567 mtn and send 5k to Mum -> buy_data + send_money."
)

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Save-beneficiary prompt + "Hi" -> conversational.
- Recent surface + short follow-up -> ground against RECENT_CONTEXT before new task.
- Recent list + "is that all/any more/show details/the first one" -> answer/select from list.
- Active transfer flow + "send it to her" -> send_money with selector reference.
- Resume prompt + "continue" -> stay on current transactional flow."""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return schema JSON"

PROMPT_BUNDLE_ORDER = (
    "transfer_only",
    "mixed_tx",
    "money_move",
    "context",
    "executor_coverage_guard",
)
