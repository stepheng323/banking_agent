"""Task planner for breaking down user requests into executable tasks."""
import time
from typing import cast

from langchain_openai import ChatOpenAI

from shared.policy.adapters import build_planner_policy_block
from shared.policy.loader import get_cached_policy
from shared.services.task_queue.service import TaskQueueService
from shared.types.planner import InterruptRouteDecision, PlannerOutput, TurnRouteDecision
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Planner prompts
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
  - query: transaction_list | transaction_search | analytics_summary | time_comparison | beneficiary_summary | affordability
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
    "R11_RESPONSE_KEYS": "Conversational no-task outputs must set allowed response_key; cancellation uses planner.cancelled.",
    "R12_BENEFICIARY_HANDLING": "Reactive beneficiary save requires explicit save intent; avoid accidental beneficiary/support routing.",
    "R13_QUERY_CONTINUATION": "Active query continuation stays query; 'send again/resend' maps to transfer replay task.",
    "R14_REFERENCE_BINDING": "Use reference selector for pronoun/index follow-ups; preserve resolver ambiguity.",
    "R15_RESUMPTION": "Emit resume_session/dismiss_resume_session only when resume prompt is explicit in context.",
    "R16_FASTPATH_CONTEXT_READ": "Eligible read-only context answers may return conversational tasks=[].",
    "R17_FASTPATH_FALLBACK": "If context is incomplete/stale, route to worker task (no guessing).",
    "R18_FASTPATH_SUBTYPE": "Set context_fastpath_subtype only for eligible context-read answers.",
    "R19_TRANSFER_FIDELITY": "For transfer, preserve typed recipient string; do not expand from user state.",
    "R20_TRANSFER_ACCOUNT_BANK": "If account+bank are in the same utterance, populate recipient_account and bank_name.",
    "R21_TRANSFER_SCHEDULING": "Map future/repeating/list/cancel schedule intents to scheduling transfer actions.",
    "R22_MIXED_MONEY_MOVE": "Explicit mixed transfer/airtime/data requests must emit all mentioned transaction tasks in order.",
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
- Send 10k to Mum and buy 5k airtime -> mixed, t1 transfer send_money amount=10000 recipient="Mum" | t2 airtime buy_airtime amount=5000
- Buy 5k airtime then send 10k to Mum -> mixed, t1 airtime buy_airtime amount=5000 | t2 transfer send_money amount=10000 depends_on=["t1"]
- Send 10k to Mum tomorrow 9am -> transfer, t1 schedule_transfer amount=10000 recipient="Mum" schedule="tomorrow 9am"
- Send 10k to Mum every Friday -> transfer, t1 recurring_transfer amount=10000 recipient="Mum" schedule="every friday" recurring=true
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


PLANNER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

INTERRUPT_ROUTER_SYSTEM_PROMPT = """You classify pending-input turns for an active banking flow.
Return ONLY JSON for this schema:
- decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- target_intent: transfer | airtime | data | query | account | support | faq |
  beneficiary | conversational | cancel | mixed | null
- target_mode: new | continuation | null
- status_query_type: recap | requirements | null
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
13) If user asks for flow status (e.g. "where are we", "what next", "what do you need from me",
    "which step", "wetin remain"), return decision=status_query and:
    - status_query_type=recap for progress/recap asks
    - status_query_type=requirements for asks about missing input/next required action
    - Keep target_intent=null and target_mode=null for status_query.
"""

INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Pending context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

TURN_ROUTER_SYSTEM_PROMPT = """You are a lightweight pre-planner router for a multilingual Nigerian banking assistant.

Return ONLY JSON with:
- decision: go_planner | respond_directly | query_continuation
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- response_key: conversational.greeting | conversational.appreciation |
  conversational.checkin | conversational.identity |
  conversational.brand_origin | conversational.capability_question |
  conversational.out_of_scope | conversational.clarify | planner.cancelled | null
- response: short direct response text or null
- expected_transaction_executors: array of transfer|airtime|data (empty if none)
- reason: short reason

Rules:
1) Use decision=respond_directly only for obvious conversational/meta responses.
2) Use decision=query_continuation only for clear query continuation turns.
3) Otherwise use decision=go_planner.
4) Populate expected_transaction_executors only when user explicitly asks those transaction actions.
4b) For explicit mixed transaction requests, include every mentioned executor in expected_transaction_executors.
    Example: "send 10k to mum and buy 5k airtime" -> ["transfer","airtime"].
5) Be multilingual and semantic; avoid English-only assumptions.
6) If uncertain, choose go_planner with empty expected_transaction_executors.
"""

TURN_ROUTER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Pre-planner context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

QUOTED_REPLAY_SYSTEM_PROMPT = """You interpret quoted follow-up banking messages for replay execution.

You receive:
- user message
- quoted actionable payload (authoritative seed from the quoted outbound message)

Goal:
- decide if the user is asking to replay/modify that quoted action
- when yes, return executable domain task payloads directly for workers

You must reason semantically across languages (English, Pidgin, Yoruba, Hausa, Igbo, French).
Do not use brittle keyword-only heuristics.

Return ONLY JSON matching:
- decision: not_replay | execute | clarify
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- tasks: list of executable tasks (empty unless decision=execute)
  - each task: {task_type: transfer|airtime|data, payload: object}
- clarify_message: short user-facing clarification when decision=clarify, else null
- reason: short internal reason

Rules:
1) If user message is unrelated to replaying the quoted action, decision=not_replay.
2) If user clearly asks to replay/modify quoted action, decision=execute and provide worker-ready tasks.
3) Use quoted actionable payload as the base truth, then apply user-requested modifications.
4) Include only tasks relevant to user's request; support single or multi-action execution.
5) If intent is ambiguous or unsafe to execute confidently, decision=clarify with clarify_message.
6) Never output support tasks; only transfer|airtime|data tasks.
"""

QUOTED_REPLAY_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Quoted context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

class TaskPlanner:
    """Handles task planning for multi-step requests."""

    def __init__(
        self,
        planner_llm: ChatOpenAI,
        interrupt_llm: ChatOpenAI | None = None,
        task_queue_service: TaskQueueService | None = None,
    ) -> None:
        self.planner_llm = planner_llm
        self.interrupt_llm = interrupt_llm or planner_llm
        self.structured_planner = planner_llm.with_structured_output(PlannerOutput)
        self.structured_turn_router = self.interrupt_llm.with_structured_output(TurnRouteDecision)
        self.structured_interrupt_router = self.interrupt_llm.with_structured_output(InterruptRouteDecision)
        self.structured_quoted_replay = planner_llm.with_structured_output(QuotedReplayInterpretation)
        self.task_queue_service = task_queue_service

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        """
        Use planner to break down request into tasks.

        Args:
            phone_number: User's phone number
            text: User's message
            context: Current flow state/context summary

        Returns:
            PlannerOutput with planned tasks
        """
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(phone_number=phone_number, user_message=text, context=context)
        system_prompt, system_prompt_profile = build_runtime_planner_system_prompt(text, context)
        start = time.perf_counter()
        result = await self.structured_planner.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "planner_llm_call",
            duration_ms=round(duration_ms, 2),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            prompt_profile=system_prompt_profile,
            baseline_runtime_system_chars=len(PLANNER_RUNTIME_BASELINE_PROMPT),
            baseline_runtime_profile=PLANNER_RUNTIME_BASELINE_PROFILE,
        )

        if isinstance(result, PlannerOutput):
            return result
        return cast(PlannerOutput, PlannerOutput.model_validate(result))

    async def route_turn(self, phone_number: str, text: str, context: str = "None") -> TurnRouteDecision:
        """Lightweight pre-planner routing for ambiguous/meta turns."""
        user_prompt = TURN_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = TURN_ROUTER_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_turn_router.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "preplanner_turn_router_llm_call",
            duration_ms=round(duration_ms, 2),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
        )
        if isinstance(result, TurnRouteDecision):
            return result
        return cast(TurnRouteDecision, TurnRouteDecision.model_validate(result))

    async def route_pending_input(self, phone_number: str, text: str, context: str = "None") -> InterruptRouteDecision:
        """Classify whether pending-input turn should continue current flow or switch intent."""
        user_prompt = INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = INTERRUPT_ROUTER_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_interrupt_router.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "interrupt_router_llm_call",
            duration_ms=round(duration_ms, 2),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
        )
        if isinstance(result, InterruptRouteDecision):
            return result
        return cast(InterruptRouteDecision, InterruptRouteDecision.model_validate(result))

    async def interpret_quoted_replay(
        self, phone_number: str, text: str, context: str = "None"
    ) -> QuotedReplayInterpretation:
        """Interpret a quoted follow-up turn for replay semantics."""
        user_prompt = QUOTED_REPLAY_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = QUOTED_REPLAY_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_quoted_replay.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "quoted_replay_llm_call",
            duration_ms=round(duration_ms, 2),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
        )
        if isinstance(result, QuotedReplayInterpretation):
            parsed = result
        else:
            parsed = cast(QuotedReplayInterpretation, QuotedReplayInterpretation.model_validate(result))
        logger.info(
            "quoted_replay_decision",
            decision=parsed.decision,
            confidence=parsed.confidence,
            detected_language=parsed.detected_language,
            tasks=len(parsed.tasks),
        )
        return parsed

OrchestratorTaskPlanner = TaskPlanner
