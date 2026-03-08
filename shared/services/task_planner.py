"""Task planner for breaking down user requests into executable tasks."""
import time
from typing import cast

from langchain_openai import ChatOpenAI

from shared.services.task_planner_prompts import (
    PLANNER_RULE_ATOMS,
    PLANNER_RUNTIME_BASELINE_PROFILE,
    PLANNER_RUNTIME_BASELINE_PROMPT,
    build_runtime_planner_system_prompt,
    refresh_planner_system_prompt,
)
from shared.services.task_queue.service import TaskQueueService
from shared.types.planner import InterruptRouteDecision, PlannerOutput, TurnRouteDecision
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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
14) In confirmation/auth transaction flows, treat concise correction replies as continue_flow
    (target_intent=null), not switch_intent. Examples: "make it 20k", "change amount to 13k",
    "use opay instead", "it's for feeding".
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

__all__ = [
    "INTERRUPT_ROUTER_SYSTEM_PROMPT",
    "PLANNER_RULE_ATOMS",
    "PLANNER_RUNTIME_BASELINE_PROFILE",
    "PLANNER_RUNTIME_BASELINE_PROMPT",
    "QUOTED_REPLAY_SYSTEM_PROMPT",
    "TaskPlanner",
    "TURN_ROUTER_SYSTEM_PROMPT",
    "build_runtime_planner_system_prompt",
    "refresh_planner_system_prompt",
    "OrchestratorTaskPlanner",
]
