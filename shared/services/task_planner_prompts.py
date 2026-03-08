"""Prompt assets and runtime prompt builder for task planning."""

from shared.policy.adapters import build_planner_policy_block
from shared.policy.loader import get_cached_policy

PLANNER_RUNTIME_SCHEMA_PROMPT = """You are an intent classifier + task planner for a Nigerian digital bank assistant.
Classify intent, detect language, and output executable tasks.

## OUTPUT (ALL REQUIRED)
- primary_intent: transfer | airtime | data | query | beneficiary | account | support | faq | orchestrator |
  conversational | cancel | mixed
- response_key: conversational.greeting | conversational.appreciation | conversational.checkin |
  conversational.identity | conversational.brand_origin | conversational.capability_question |
  conversational.out_of_scope | conversational.clarify | planner.cancelled | null
- response: short acknowledgment in the user's language (required for compatibility)
- confidence: 0.0-1.0
- is_complex: true when multi-intent/recipient
- is_cancellation: true only for explicit cancel words
- is_confirmation: true only for pure approval with no new details
- detected_language: English | Yoruba | Hausa | Igbo | Pidgin | French
- context_fastpath_subtype: null OR
  account_count | linked_accounts_summary | default_account_identity | pending_mandate_explanation |
  account_mandate_readiness_summary | account_linked_bank_existence_check | beneficiary_count |
  beneficiary_list | beneficiary_existence_check | beneficiary_name_match_preview | flow_recap |
  flow_missing_requirements
- normalized_instruction: cleaned user request
- tasks: task list (can be [] for conversational direct responses)

## TASK CONTRACT
- Banking intents must emit task(s) even when slots are missing; workers handle slot filling.
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
  - orchestrator: resume_session | dismiss_resume_session

## EXTRACTION PRECISION RULES
- Transfer recipient fidelity: keep recipient exactly as user said ("mum", "tolu"); do not expand from context.
- Narration is optional; never invent it.
- Pronoun/index follow-up should use reference selector:
  - {"selector":"previous"} for him/her/that/it
  - {"selector":"index","index":N} for first/2nd/item N
- Parse source_bank_name and source_account_index when explicitly provided.
- Normalize amounts (5k->5000).
- For explicit mixed transfer/airtime/data requests, emit one task per explicit action and preserve order.
- Apply rules semantically across English, Pidgin, Yoruba, Hausa, Igbo, and French.
"""

PLANNER_RULE_ATOMS: dict[str, str] = {
    "R01_CONVERSATIONAL": "Greetings/thanks/check-ins -> conversational with tasks=[].",
    "R02_BANKING_TASKS": "Banking intents emit at least one executable task.",
    "R03_MISSING_SLOTS": "If slots are missing, still create task; workers will fill slots.",
    "R04_DEPENDENCIES": "Encode explicit sequencing with depends_on.",
    "R05_CANCEL_CONFIRM": "is_cancellation only for explicit cancel; is_confirmation only for pure approval.",
    "R06_AMOUNT_NORMALIZATION": "Normalize shorthand amounts (5k->5000).",
    "R07_OUT_OF_SCOPE": "Non-banking asks -> conversational + response_key=conversational.out_of_scope.",
    "R08_ACTION_EXECUTOR": "Action must belong to the selected executor.",
    "R09_CONTEXT_OVERRIDE": "In active flows, treat replies as slot updates unless clear switch/cancel.",
    "R10_LANGUAGE_ALIGNMENT": "Detect language correctly and align response language.",
    "R11_RESPONSE_KEYS": (
        "Conversational no-task outputs must set allowed response_key; "
        "cancellation uses planner.cancelled."
    ),
    "R12_BENEFICIARY_HANDLING": (
        "Reactive beneficiary save requires explicit save intent; "
        "avoid accidental beneficiary/support routing."
    ),
    "R13_QUERY_CONTINUATION": (
        "Active query continuation stays query; "
        "'send again/resend' maps to transfer replay task."
    ),
    "R14_REFERENCE_BINDING": "Use reference selector for pronoun/index follow-ups; preserve resolver ambiguity.",
    "R15_RESUMPTION": "Emit resume_session/dismiss_resume_session only when resume prompt is explicit in context.",
    "R16_FASTPATH_CONTEXT_READ": "Eligible read-only context answers may return conversational tasks=[].",
    "R17_FASTPATH_FALLBACK": "If context is incomplete/stale, route to worker task (no guessing).",
    "R18_FASTPATH_SUBTYPE": "Set context_fastpath_subtype only for eligible context-read answers.",
    "R19_TRANSFER_FIDELITY": "For transfer, preserve typed recipient string; do not expand from user state.",
    "R20_TRANSFER_ACCOUNT_BANK": "If account+bank are in the same utterance, populate recipient_account and bank_name.",
    "R21_TRANSFER_SCHEDULING": "Map future/repeating/list/cancel schedule intents to scheduling transfer actions.",
    "R22_MIXED_MONEY_MOVE": (
        "Explicit mixed transfer/airtime/data requests must emit all mentioned "
        "transaction tasks in order."
    ),
    "R23_MULTILINGUAL_SAFETY": "Apply rules semantically across English, Pidgin, Yoruba, Hausa, Igbo, French.",
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
    "R19_TRANSFER_FIDELITY",
    "R20_TRANSFER_ACCOUNT_BANK",
    "R21_TRANSFER_SCHEDULING",
    "R22_MIXED_MONEY_MOVE",
    "R23_MULTILINGUAL_SAFETY",
}

PLANNER_MONEY_MOVE_RULE_ATOMS = {"R09_CONTEXT_OVERRIDE", "R14_REFERENCE_BINDING"}
PLANNER_QUERY_RULE_ATOMS = {"R13_QUERY_CONTINUATION", "R14_REFERENCE_BINDING"}
PLANNER_CONTEXT_RULE_ATOMS = {"R09_CONTEXT_OVERRIDE", "R12_BENEFICIARY_HANDLING", "R15_RESUMPTION"}

PLANNER_RUNTIME_COMMON_EXAMPLES = """## TARGETED EXAMPLES (COMMON)
- How far -> conversational, response_key=conversational.checkin, detected_language=Pidgin
- Send 8k -> transfer, t1 send_money amount=8000 (recipient omitted)
- Buy 1k airtime -> airtime, t1 buy_airtime amount=1000 MONEY_MOVE
- Get 2GB data -> data, t1 buy_data plan="2GB" MONEY_MOVE
- What is my balance -> account, t1 check_balance READ_ONLY
"""

PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES = """## TARGETED EXAMPLES (MONEY_MOVE)
- Send 10k to Mum and buy 5k airtime -> mixed,
  t1 transfer send_money amount=10000 recipient="Mum" | t2 airtime buy_airtime amount=5000
- Buy 5k airtime then send 10k to Mum -> mixed,
  t1 airtime buy_airtime amount=5000 | t2 transfer send_money amount=10000 depends_on=["t1"]
- Send 10k to Mum tomorrow 9am -> transfer, t1 schedule_transfer amount=10000 recipient="Mum" schedule="tomorrow 9am"
- Send 10k to Mum every Friday -> transfer,
  t1 recurring_transfer amount=10000 recipient="Mum" schedule="every friday" recurring=true
- Show scheduled transfers -> transfer list_scheduled_transfers; Cancel schedule 2 -> transfer cancel_scheduled_transfer
"""

PLANNER_RUNTIME_QUERY_EXAMPLES = """## TARGETED EXAMPLES (QUERY)
- show my transactions -> query, t1 transaction_list READ_ONLY
- how much did i spend last week -> query, t1 analytics_summary READ_ONLY
- Active Query Session + "any credits?" -> query continuation/refinement
- Active Query Session + "send again" -> transfer replay task
"""

PLANNER_RUNTIME_CONTEXT_EXAMPLES = """## TARGETED EXAMPLES (CONTEXT)
- Asked to save beneficiary + "save as Gaines" -> beneficiary, t1 save_beneficiary alias="Gaines"
- Asked to save beneficiary + "Hi" -> conversational, response_key=conversational.greeting
- Asked to resume transfer + "Yes" -> orchestrator, t1 resume_session
- Recent Chat account_count + "List them" -> conversational, context_fastpath_subtype=linked_accounts_summary
- Recent Chat beneficiary_count + "List them" -> conversational, context_fastpath_subtype=beneficiary_list
"""

PLANNER_RUNTIME_PROMPT_SUFFIX = "Return ONLY JSON matching the schema."

MONEY_MOVE_PROMPT_MARKERS = (
    "transfer",
    "send",
    "pay",
    "airtime",
    "recharge",
    "credit",
    "data",
    "buy",
    "schedule",
)
QUERY_PROMPT_MARKERS = (
    "transaction",
    "transactions",
    "history",
    "spend",
    "spent",
    "debit",
    "credit",
    "receipt",
    "analytics",
    "expense",
    "more",
    "next",
)
CONTEXT_PROMPT_MARKERS = (
    "asked to save beneficiary",
    "asked to resume",
    "active query session",
    "active flow",
    "recent chat",
    "user state",
)


def _normalize_prompt_signal(value: str) -> str:
    return " ".join(value.lower().split())


def _should_include_money_move_examples(text: str, context: str) -> bool:
    normalized = _normalize_prompt_signal(f"{text} {context}")
    return any(marker in normalized for marker in MONEY_MOVE_PROMPT_MARKERS)


def _should_include_query_examples(text: str, context: str) -> bool:
    normalized = _normalize_prompt_signal(f"{text} {context}")
    return any(marker in normalized for marker in QUERY_PROMPT_MARKERS)


def _should_include_context_examples(context: str) -> bool:
    normalized = _normalize_prompt_signal(context)
    return any(marker in normalized for marker in CONTEXT_PROMPT_MARKERS)


def _select_runtime_rule_atoms(text: str, context: str) -> list[str]:
    selected = set(PLANNER_BASE_RULE_ATOMS)
    if _should_include_money_move_examples(text, context):
        selected.update(PLANNER_MONEY_MOVE_RULE_ATOMS)
    if _should_include_query_examples(text, context):
        selected.update(PLANNER_QUERY_RULE_ATOMS)
    if _should_include_context_examples(context):
        selected.update(PLANNER_CONTEXT_RULE_ATOMS)
    return [rule_id for rule_id in PLANNER_RULE_ATOM_ORDER if rule_id in selected]


def _compile_rule_atoms(rule_ids: list[str]) -> str:
    lines = ["## COMPILED RULE ATOMS"]
    for rule_id in rule_ids:
        lines.append(f"- {rule_id}: {PLANNER_RULE_ATOMS[rule_id]}")
    return "\n".join(lines)


def _build_planner_policy_block() -> str:
    return build_planner_policy_block(get_cached_policy())


PLANNER_POLICY_BLOCK = _build_planner_policy_block()


def build_runtime_planner_system_prompt(text: str, context: str) -> tuple[str, str]:
    rule_ids = _select_runtime_rule_atoms(text, context)
    compiled_rules = _compile_rule_atoms(rule_ids)
    sections = [
        PLANNER_POLICY_BLOCK,
        PLANNER_RUNTIME_SCHEMA_PROMPT,
        compiled_rules,
        PLANNER_RUNTIME_COMMON_EXAMPLES,
    ]
    profile_parts = ["schema", f"rules_{len(rule_ids)}", "ex_common"]
    if _should_include_money_move_examples(text, context):
        sections.append(PLANNER_RUNTIME_MONEY_MOVE_EXAMPLES)
        profile_parts.append("ex_money_move")
    if _should_include_query_examples(text, context):
        sections.append(PLANNER_RUNTIME_QUERY_EXAMPLES)
        profile_parts.append("ex_query")
    if _should_include_context_examples(context):
        sections.append(PLANNER_RUNTIME_CONTEXT_EXAMPLES)
        profile_parts.append("ex_context")
    sections.append(PLANNER_RUNTIME_PROMPT_SUFFIX)
    return "\n\n".join(sections), "+".join(profile_parts)


PLANNER_RUNTIME_BASELINE_PROMPT = ""
PLANNER_RUNTIME_BASELINE_PROFILE = ""


def _refresh_runtime_prompt_baseline() -> None:
    global PLANNER_RUNTIME_BASELINE_PROMPT, PLANNER_RUNTIME_BASELINE_PROFILE
    PLANNER_RUNTIME_BASELINE_PROMPT, PLANNER_RUNTIME_BASELINE_PROFILE = build_runtime_planner_system_prompt("", "None")


_refresh_runtime_prompt_baseline()


def refresh_planner_system_prompt() -> None:
    """Refresh policy block and runtime prompt baseline after policy reload."""
    global PLANNER_POLICY_BLOCK
    PLANNER_POLICY_BLOCK = _build_planner_policy_block()
    _refresh_runtime_prompt_baseline()


__all__ = [
    "PLANNER_RULE_ATOMS",
    "PLANNER_RUNTIME_BASELINE_PROMPT",
    "PLANNER_RUNTIME_BASELINE_PROFILE",
    "build_runtime_planner_system_prompt",
    "refresh_planner_system_prompt",
]
