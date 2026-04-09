"""Task planner for breaking down user requests into executable tasks."""

import time
from typing import Any, Literal, cast

from langchain_openai import ChatOpenAI

from shared.services.task_planner_normalizer import normalize_planner_transaction_output
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
    INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT,
    INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL,
    INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE,
    QUOTED_REPLAY_SYSTEM_PROMPT,
    QUOTED_REPLAY_USER_PROMPT_TEMPLATE,
    SEMANTIC_ROUTER_SYSTEM_PROMPT,
    SEMANTIC_ROUTER_USER_PROMPT_TEMPLATE,
)
from shared.services.task_queue.service import TaskQueueService
from shared.types.planner import InterruptRouteDecision, PlannerOutput, SemanticRouteDecision
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


PLANNER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Context: {context}
Message: \"\"\"{user_message}\"\"\"
"""


def _with_structured_output(
    llm: ChatOpenAI,
    schema: type[Any],
    *,
    method: Literal["function_calling", "json_mode", "json_schema"] | None = None,
) -> Any:
    if method is None:
        return llm.with_structured_output(schema)
    try:
        return llm.with_structured_output(schema, method=method)
    except TypeError:
        return llm.with_structured_output(schema)


class TaskPlanner:
    """Handles task planning for multi-step requests."""

    def __init__(
        self,
        planner_llm: ChatOpenAI,
        semantic_router_llm: ChatOpenAI | None = None,
        interrupt_llm: ChatOpenAI | None = None,
        task_queue_service: TaskQueueService | None = None,
    ) -> None:
        self.planner_llm = planner_llm
        self.semantic_router_llm = semantic_router_llm or interrupt_llm or planner_llm
        self.interrupt_llm = interrupt_llm or planner_llm
        self.uses_dedicated_interrupt_model = interrupt_llm is not None
        self.uses_dedicated_semantic_router_model = semantic_router_llm is not None
        # PlannerOutput now includes clause-local free-form extracted fields. That shape is valid for
        # tool/function calling, but OpenAI's strict response_format schema rejects it.
        self.structured_planner = _with_structured_output(
            planner_llm,
            PlannerOutput,
            method="function_calling",
        )
        self.structured_semantic_router = _with_structured_output(
            self.semantic_router_llm,
            SemanticRouteDecision,
        )
        self.structured_interrupt_router = _with_structured_output(
            self.interrupt_llm,
            InterruptRouteDecision,
        )
        self.structured_quoted_replay = _with_structured_output(
            planner_llm,
            QuotedReplayInterpretation,
        )
        self.task_queue_service = task_queue_service
        if not self.uses_dedicated_interrupt_model:
            logger.warning("interrupt_router_model_not_dedicated", mode="planner_fallback")
        if not self.uses_dedicated_semantic_router_model:
            logger.warning("semantic_router_model_not_dedicated", mode="interrupt_or_planner_fallback")

    @staticmethod
    def _log_latency_span(*, span: str, duration_ms: float, path_label: str) -> None:
        logger.info(
            "perf_timer_latency",
            gate=span,
            span=span,
            duration_ms=round(duration_ms, 2),
            path_label=path_label,
        )

    @staticmethod
    def _model_name(llm: Any) -> str | None:
        return cast(str | None, getattr(llm, "model_name", None) or getattr(llm, "model", None))

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: PlannerPromptSignals,
        path_label: str = "planner_path",
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
            model=self._model_name(self.planner_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if prompt_signals.compact_context else "full",
            prompt_profile=prompt_result.profile,
            prompt_bundles=list(prompt_result.selected_bundle_ids),
            prompt_rule_count=len(prompt_result.selected_rule_ids),
            baseline_runtime_system_chars=PLANNER_PROMPT_BASELINE_RESULT.char_count,
            baseline_runtime_profile=PLANNER_PROMPT_BASELINE_RESULT.profile,
        )
        self._log_latency_span(span="planner_llm", duration_ms=duration_ms, path_label=path_label)

        if isinstance(result, PlannerOutput):
            return normalize_planner_transaction_output(result, text)
        parsed = cast(PlannerOutput, PlannerOutput.model_validate(result))
        return normalize_planner_transaction_output(parsed, text)

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "direct_path",
    ) -> SemanticRouteDecision:
        """Top-level semantic routing before planner-owned dispatch."""
        user_prompt = SEMANTIC_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = SEMANTIC_ROUTER_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_semantic_router.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "semantic_router_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if context == "None" else "full",
        )
        self._log_latency_span(span="semantic_router_llm", duration_ms=duration_ms, path_label=path_label)
        if isinstance(result, SemanticRouteDecision):
            return result
        return cast(SemanticRouteDecision, SemanticRouteDecision.model_validate(result))

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        """Classify whether pending-input turn should continue current flow or switch intent."""
        user_prompt = INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = (
            INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT
            if prompt_mode == "compact"
            else INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
        )
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
            model=self._model_name(self.interrupt_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if context == "None" else "full",
            prompt_mode=prompt_mode,
        )
        self._log_latency_span(span="interrupt_router_llm", duration_ms=duration_ms, path_label=path_label)
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
            model=self._model_name(self.planner_llm),
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
    "INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT",
    "INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL",
    "INTERRUPT_ROUTER_SYSTEM_PROMPT",
    "PLANNER_PROMPT_BASELINE_RESULT",
    "PLANNER_RULE_ATOMS",
    "PlannerPromptBuildInput",
    "PlannerPromptSignals",
    "QUOTED_REPLAY_SYSTEM_PROMPT",
    "TaskPlanner",
    "SEMANTIC_ROUTER_SYSTEM_PROMPT",
    "build_planner_system_prompt",
    "refresh_planner_system_prompt",
    "OrchestratorTaskPlanner",
]
