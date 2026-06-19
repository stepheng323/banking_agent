"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """## OUTPUT JSON
- PlannerOutput JSON only.
- greet|thanks|checkin -> conversational,tasks=[]; banking/missing -> task+.
- mixed asks: clauses[] before tasks[]; tasks may set source_clause_index.
- task requires action; never output executor.
- transfer: send_money|schedule_transfer|recurring_transfer.
- schedule actions: list_scheduled_transactions|find_scheduled_transaction|cancel_scheduled_transaction|
  edit_scheduled_transaction; count/existence->schedule_response_mode=count.
- response_shape: count/bool/status/recap; list/detail/page/action.
- airtime: buy_airtime|schedule_airtime|recurring_airtime; data: buy_data|schedule_data|recurring_data.
- beneficiary: list/add/delete/update/save; query: list/search/analytics/time/beneficiary/affordability.
- beneficiary_route: beneficiary_list|recipient_ranking|none; people batch->recipient_allocations.
- funding split->explicit_split; mixed->depends_on."""

PLANNER_TRANSFER_PRECISION_PROMPT = """## MONEY_MOVE PRECISION
- Keep recipient exact.
- Selector refs: previous or index.
- explicit_split=source funding.
- Don't guess."""

PLANNER_TRANSFER_ONLY_PRECISION_PROMPT = """## TRANSFER_ONLY PRECISION
- 2k=2000; Tolu Access/Tolu GTB are aliases.
- Use/from/with <bank> to send -> source_bank_name only; omit bank_name unless destination account+bank.
- Each/split->recipient_allocations."""

PLANNER_MIXED_TX_PRECISION_PROMPT = """## MIXED_TX PRECISION
- clauses[] in user order; emit every explicit tx.
- Transfer+airtime/data text must emit transfer task plus purchase task; never collapse.
- Keep read-only query/balance separate; exact transfer recipient; 2k=2000,10k=10000.
- Extract phone/network. Trailing/global source "from my <bank>" applies to every transaction task.
- source phrase -> source_bank_name, not bank_name.
- Don't copy slots across clauses; don't drop read-only; don't guess."""

PLANNER_OUTPUT_QUALITY_PROMPT = (
    "## CLEAN_TX_OUTPUT\n"
    "- Task: include action, omit executor.\n"
    "- Alias exact; each/split -> recipient_allocations=Tolu Access:2000,Tolu GTB:2000; omit bank_name.\n"
    "- Alias bank words stay alias, not bank_name: Tolu Access=>recipient_name=Tolu Access.\n"
    "- Use/from/with bank->source_bank_name; omit bank_name; 1GB/500MB->plan not amount."
)

PLANNER_MIXED_OUTPUT_QUALITY_PROMPT = (
    "## CLEAN_TX_OUTPUT\n"
    "- Task: include action, omit executor.\n"
    "- Alias exact; source bank->source_bank_name; 1GB/500MB->plan not amount."
)

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
        "harmless_non_banking->conversational.casual_chat; "
        "unsupported_non_banking->conversational.out_of_scope; "
        "unsupported_capability(lending/investments/financial_advice/international_transfers/pdf_exports/"
        "csv_exports/all_time_history)->set unsupported_capability field"
    ),
    "R08_ACTION_EXECUTOR": "canonical action determines fixed runtime executor; never invent executor-like actions",
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
        "list|count|find|cancel|delete|edit scheduled->schedule action "
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
    "R28_RESPONSE_SHAPE": "how_many=count; any=bool; show/list=list; last/latest=detail",
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
    "R28_RESPONSE_SHAPE",
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
    '- Active transfer flow + "send it to her" -> {"selector":"previous"}.\n'
    '- Active transfer flow + "make it 20k" -> amount=20000.\n'
    "- Send 10k to Mum tomorrow 9am -> schedule_transfer.\n"
    "- Send half my gtb balance to Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- Bami fi idaji owo gtb mi ranse si Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- Tura rabin kudin gtb dina zuwa Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- Send it to her every Friday -> recurring_transfer.\n"
    "- Split 20k between Adebayo and Mum -> action=send_money,"
    "recipient_allocations=Adebayo:10000,Mum:10000.\n"
    "- Split 20k from Access and GTB -> explicit_split={Access:10000,GTB:10000}.\n"
    "- Biko buy 3k airtime for my line mtn -> buy_airtime."
)

PLANNER_RUNTIME_TRANSFER_ONLY_EXAMPLES = (
    "## TARGETED EXAMPLES (TRANSFER_ONLY)\n"
    "- Use GTBank to send 5k to Tolu Access for lunch -> action=send_money,source_bank_name=GTBank,"
    "recipient_name=Tolu Access,narration=Lunch; omit bank_name.\n"
    "- Send half my gtb balance to Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- Bami fi idaji owo gtb mi ranse si Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- Tura rabin kudin gtb dina zuwa Adebayo -> action=send_money,"
    "transfer_percentage=50,source_bank_name=GTBank,recipient_name=Adebayo.\n"
    "- 2k each -> action=send_money,"
    "recipient_allocations=[{recipient_name:Tolu Access,amount:2000},{recipient_name:Tolu GTB,amount:2000}],"
    "omit bank_name."
)

PLANNER_RUNTIME_MIXED_TX_EXAMPLES = (
    "## TARGETED EXAMPLES (MIXED_TX)\n"
    "- Send 10k to Tolu Access + buy 1k airtime for me -> send_money recipient_name=Tolu Access "
    "+ buy_airtime amount=1000,is_self=true.\n"
    "- Send 10k to adebayo and buy me 2k airtime from my gtb -> action=send_money amount=10000,"
    "recipient_name=adebayo,source_bank_name=GTBank + action=buy_airtime amount=2000,is_self=true,"
    "source_bank_name=GTBank.\n"
    "- Buy 1GB MTN data for me and send 2k to Mum -> buy_data plan=1GB,network=MTN,is_self=true "
    "+ send_money recipient_name=Mum,amount=2000."
)

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Save-beneficiary prompt + "Hi" -> conversational.
- Recent surface + short follow-up -> ground against RECENT_CONTEXT before new task.
- Recent list + "more/details/first one" -> answer/select from list.
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
