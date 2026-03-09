"""Prompt atoms for planner system prompt compilation."""

PLANNER_RUNTIME_SCHEMA_PROMPT = """You are an intent classifier + task planner for a Nigerian digital bank assistant.
Classify intent, detect language, and output executable tasks as JSON.

## OUTPUT (ALL REQUIRED)
- primary_intent: transfer | airtime | data | query | beneficiary | account | support | faq | orchestrator |
  conversational | cancel | mixed
- response_key: conversational.greeting | conversational.appreciation | conversational.checkin |
  conversational.identity | conversational.brand_origin | conversational.capability_question |
  conversational.out_of_scope | conversational.clarify | planner.cancelled | null
- response: short acknowledgment in detected language
- confidence: 0.0-1.0
- is_complex: true when multi-intent/recipient
- is_cancellation: true only for explicit cancel words
- is_confirmation: true only for pure approval with no new details
- detected_language: English | Yoruba | Hausa | Igbo | Pidgin | French
- context_fastpath_subtype: null | account_count | linked_accounts_summary | default_account_identity |
  pending_mandate_explanation | account_mandate_readiness_summary |
  account_linked_bank_existence_check | beneficiary_count | beneficiary_list |
  beneficiary_existence_check | beneficiary_name_match_preview | flow_recap | flow_missing_requirements
- normalized_instruction: cleaned user request
- tasks: task list (can be [] for conversational direct responses)

## TASK CONTRACT
- Banking intents must emit task(s) even when slots are missing.
- Task fields: task_id, action, executor, instruction, parameters, depends_on, risk.
- action must match executor.
- Allowed executor/action map:
  - transfer: send_money | schedule_transfer | recurring_transfer | list_scheduled_transfers | cancel_scheduled_transfer
  - airtime: buy_airtime
  - data: buy_data
  - account: check_balance | list_accounts | link_account | set_default | unlink_account
  - query: transaction_list | transaction_search | analytics_summary | time_comparison |
    beneficiary_summary | affordability
  - beneficiary: save_beneficiary | list_beneficiaries | add_beneficiary | delete_beneficiary
  - support: report_issue
  - faq: answer_faq
  - orchestrator: resume_session | dismiss_resume_session"""

PLANNER_TRANSFER_PRECISION_PROMPT = """## EXTRACTION PRECISION (MONEY_MOVE)
- Keep transfer recipient exactly as typed ("mum", "tolu"); do not expand from context.
- Narration is optional; never invent it.
- Pronoun/index selector: {"selector":"previous"} or {"selector":"index","index":N}."""

PLANNER_EXECUTOR_COVERAGE_GUARD_PROMPT = (
    "## EXECUTOR COVERAGE GUARD\n"
    "- Expected explicit transaction executors from turn-router: {expected_executors}.\n"
    "- Emit tasks that include ALL expected executors when user intent is explicit."
)

PLANNER_RULE_ATOMS: dict[str, str] = {
    "R01_CONVERSATIONAL": "Greetings/thanks/check-ins -> conversational, tasks=[].",
    "R02_BANKING_TASKS": "Banking intents must emit at least one task.",
    "R03_MISSING_SLOTS": "If slots are missing, still emit a task; workers fill slots.",
    "R04_DEPENDENCIES": "Use depends_on for explicit sequencing.",
    "R05_CANCEL_CONFIRM": "is_cancellation only for explicit cancel; is_confirmation only for pure approval.",
    "R06_AMOUNT_NORMALIZATION": "Normalize shorthand amounts (5k->5000).",
    "R07_OUT_OF_SCOPE": "Non-banking asks -> conversational with response_key=conversational.out_of_scope.",
    "R08_ACTION_EXECUTOR": "Action must match executor.",
    "R09_CONTEXT_OVERRIDE": "In active flows, treat replies as slot updates unless clear switch/cancel.",
    "R10_LANGUAGE_ALIGNMENT": "Detect language and align response language.",
    "R11_RESPONSE_KEYS": (
        "Conversational no-task outputs must set allowed response_key; cancellation uses planner.cancelled."
    ),
    "R12_BENEFICIARY_HANDLING": "Save beneficiary only on explicit save intent.",
    "R13_QUERY_CONTINUATION": "Active query follow-ups stay query; 'send again/resend' -> transfer replay.",
    "R14_REFERENCE_BINDING": "For pronoun/index follow-ups, emit selector references.",
    "R15_RESUMPTION": "Use resume_session/dismiss_resume_session only when resume prompt is explicit.",
    "R16_FASTPATH_CONTEXT_READ": "Eligible read-only context answers may return conversational tasks=[].",
    "R17_FASTPATH_FALLBACK": "If context is incomplete/stale, route to worker task.",
    "R18_FASTPATH_SUBTYPE": "Set context_fastpath_subtype only for eligible context-read answers.",
    "R19_TRANSFER_FIDELITY": "For transfers, keep recipient exactly as typed.",
    "R20_TRANSFER_ACCOUNT_BANK": "If account+bank appear together, set recipient_account and bank_name.",
    "R21_TRANSFER_SCHEDULING": "Future/repeating/list/cancel asks map to scheduling transfer actions.",
    "R22_MIXED_MONEY_MOVE": "Explicit mixed transfer/airtime/data asks must emit all mentioned tasks in order.",
    "R23_MULTILINGUAL_SAFETY": "Apply rules semantically across supported languages.",
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
]

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
- How far -> conversational, response_key=conversational.checkin, detected_language=Pidgin
- Send 8k -> transfer, send_money amount=8000 (recipient omitted)
- Buy 1k airtime -> airtime, buy_airtime amount=1000
- Get 2GB data -> data, buy_data plan="2GB"
- What is my balance -> account, check_balance"""

PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES = """## TARGETED EXAMPLES (MONEY_MOVE)
- Send 10k to Mum and buy 5k airtime -> mixed, transfer + airtime tasks.
- Buy 5k airtime then send 10k to Mum -> mixed; use depends_on for explicit order."""

PLANNER_RUNTIME_QUERY_EXAMPLES = """## TARGETED EXAMPLES (QUERY)
- Active Query Session + "any credits?" -> query continuation
- Active Query Session + "send again" -> transfer replay task"""

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Asked to save beneficiary + "Hi" -> conversational, response_key=conversational.greeting
- Recent Chat account_count + "List them" -> conversational, context_fastpath_subtype=linked_accounts_summary
- Recent Chat beneficiary_count + "List them" -> conversational, context_fastpath_subtype=beneficiary_list"""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return ONLY JSON matching the schema."

PROMPT_BUNDLE_ORDER = (
    "money_move",
    "query",
    "context",
    "executor_coverage_guard",
)
