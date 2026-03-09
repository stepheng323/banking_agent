"""Task planner for breaking down user requests into executable tasks."""
import time
from typing import cast

from langchain_openai import ChatOpenAI

from shared.services.task_planner_prompts import (
    PLANNER_PROMPT_BASELINE_RESULT,
    PLANNER_RULE_ATOMS,
    PlannerPromptBuildInput,
    PlannerPromptSignals,
    build_planner_system_prompt,
    refresh_planner_system_prompt,
)
from shared.services.task_planner_router_prompts import (
    INTERRUPT_ROUTER_SYSTEM_PROMPT,
    INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE,
    QUOTED_REPLAY_SYSTEM_PROMPT,
    QUOTED_REPLAY_USER_PROMPT_TEMPLATE,
    TURN_ROUTER_SYSTEM_PROMPT,
    TURN_ROUTER_USER_PROMPT_TEMPLATE,
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

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: PlannerPromptSignals,
    ) -> PlannerOutput:
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
        prompt_input = PlannerPromptBuildInput(text=text, context=context, signals=prompt_signals)
        prompt_result = build_planner_system_prompt(prompt_input)
        system_prompt = prompt_result.system_prompt
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
            prompt_profile=prompt_result.profile,
            prompt_bundles=list(prompt_result.selected_bundle_ids),
            prompt_rule_count=len(prompt_result.selected_rule_ids),
            baseline_runtime_system_chars=PLANNER_PROMPT_BASELINE_RESULT.char_count,
            baseline_runtime_profile=PLANNER_PROMPT_BASELINE_RESULT.profile,
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
    "PLANNER_PROMPT_BASELINE_RESULT",
    "PLANNER_RULE_ATOMS",
    "PlannerPromptBuildInput",
    "PlannerPromptSignals",
    "QUOTED_REPLAY_SYSTEM_PROMPT",
    "TaskPlanner",
    "TURN_ROUTER_SYSTEM_PROMPT",
    "build_planner_system_prompt",
    "refresh_planner_system_prompt",
    "OrchestratorTaskPlanner",
]
