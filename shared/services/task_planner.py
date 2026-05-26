"""Task planner for breaking down user requests into executable tasks."""

import time
from typing import Any, Literal, cast

from langchain_openai import ChatOpenAI

from shared.services.confirmation_decision import (
    ConfirmationDecision,
    ConfirmationDecisionOutput,
    ConfirmationPromptKind,
    classify_confirmation_reply,
)
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
    CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT,
    CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE,
    CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT,
    CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE,
    INTERRUPT_ROUTER_SYSTEM_PROMPT,
    INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT,
    INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL,
    INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE,
    PENDING_ACTION_EDIT_SYSTEM_PROMPT,
    PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE,
    QUOTED_REPLAY_SYSTEM_PROMPT,
    QUOTED_REPLAY_USER_PROMPT_TEMPLATE,
    SCHEDULE_READ_ROUTER_SYSTEM_PROMPT,
    SCHEDULE_READ_ROUTER_USER_PROMPT_TEMPLATE,
    SEMANTIC_ROUTER_SYSTEM_PROMPT,
    SEMANTIC_ROUTER_USER_PROMPT_TEMPLATE,
)
from shared.services.task_queue.service import TaskQueueService
from shared.services.unsupported_capabilities import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
    classify_unsupported_boundary_turn_semantic,
    classify_unsupported_capability_semantic,
)
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
    InterruptRouteDecision,
    PendingActionEditDecision,
    PlannerOutput,
    SemanticRouteDecision,
)
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
        self.structured_schedule_read_router = _with_structured_output(
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
        self.structured_context_frame_followup = _with_structured_output(
            self.semantic_router_llm,
            ContextFrameFollowupDecision,
        )
        self.structured_context_frame_replay_modifier = _with_structured_output(
            self.semantic_router_llm,
            ContextFrameReplayModifier,
        )
        self.structured_pending_action_edit = _with_structured_output(
            self.interrupt_llm,
            PendingActionEditDecision,
        )
        self.structured_confirmation_decision = _with_structured_output(
            self.interrupt_llm,
            ConfirmationDecisionOutput,
        )
        self.structured_unsupported_capability = _with_structured_output(
            self.semantic_router_llm,
            UnsupportedCapabilitySemanticOutput,
        )
        self.structured_unsupported_boundary_turn = _with_structured_output(
            self.semantic_router_llm,
            UnsupportedBoundaryTurnOutput,
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

    async def route_schedule_read_turn(
        self,
        phone_number: str,
        text: str,
        *,
        path_label: str = "direct_path",
    ) -> SemanticRouteDecision:
        """Small semantic classifier for read-only scheduled-transaction list/count turns."""
        user_prompt = SCHEDULE_READ_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
        )
        system_prompt = SCHEDULE_READ_ROUTER_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_schedule_read_router.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "schedule_read_router_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
        )
        self._log_latency_span(span="schedule_read_router_llm", duration_ms=duration_ms, path_label=path_label)
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

    async def classify_confirmation_reply(
        self,
        text: str,
        *,
        prompt_kind: ConfirmationPromptKind,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "interrupt_path",
    ) -> ConfirmationDecision:
        """Bounded LLM fallback for prompt-scoped approval/rejection replies."""
        start = time.perf_counter()
        result = await classify_confirmation_reply(
            text,
            prompt_kind=prompt_kind,
            locale=locale,
            context=context,
            structured_llm=self.structured_confirmation_decision,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "confirmation_decision_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.interrupt_llm),
            action=result.action,
            source=result.source,
            confidence=result.confidence,
            prompt_kind=prompt_kind,
            context_chars=len(context),
        )
        self._log_latency_span(span="confirmation_decision_llm", duration_ms=duration_ms, path_label=path_label)
        return result

    async def classify_unsupported_capability(
        self,
        text: str,
        *,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "direct_path",
    ) -> UnsupportedCapabilitySemanticOutput:
        """Bounded semantic classifier for unsupported capability boundaries."""
        start = time.perf_counter()
        result = await classify_unsupported_capability_semantic(
            text,
            locale=locale,
            context=context,
            structured_llm=self.structured_unsupported_capability,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "unsupported_capability_semantic_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            action=result.action,
            capability_key=result.capability_key,
            confidence=result.confidence,
            context_chars=len(context),
        )
        self._log_latency_span(
            span="unsupported_capability_semantic_llm",
            duration_ms=duration_ms,
            path_label=path_label,
        )
        return result

    async def classify_unsupported_boundary_turn(
        self,
        text: str,
        *,
        boundary_key: str,
        boundary_label: str,
        followup_count: int = 0,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "direct_path",
    ) -> UnsupportedBoundaryTurnOutput:
        """Bounded semantic classifier for turns after an unsupported capability refusal."""
        start = time.perf_counter()
        result = await classify_unsupported_boundary_turn_semantic(
            text,
            boundary_key=boundary_key,
            boundary_label=boundary_label,
            followup_count=followup_count,
            locale=locale,
            context=context,
            structured_llm=self.structured_unsupported_boundary_turn,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "unsupported_boundary_turn_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            action=result.action,
            capability_key=result.capability_key,
            confidence=result.confidence,
            boundary_key=boundary_key,
            context_chars=len(context),
        )
        self._log_latency_span(
            span="unsupported_boundary_turn_llm",
            duration_ms=duration_ms,
            path_label=path_label,
        )
        return result

    async def interpret_context_frame_followup(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameFollowupDecision:
        """Classify whether a user turn is a semantic follow-up to the latest displayed frame."""
        user_prompt = CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_context_frame_followup.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "context_frame_followup_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if context == "None" else "full",
        )
        self._log_latency_span(span="context_frame_followup_llm", duration_ms=duration_ms, path_label=path_label)
        if isinstance(result, ContextFrameFollowupDecision):
            return result
        return cast(ContextFrameFollowupDecision, ContextFrameFollowupDecision.model_validate(result))

    async def extract_context_frame_replay_modifiers(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameReplayModifier:
        """Extract a strict edit patch for frame-backed transaction replay."""
        user_prompt = CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_context_frame_replay_modifier.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "context_frame_replay_modifier_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.semantic_router_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if context == "None" else "full",
        )
        self._log_latency_span(
            span="context_frame_replay_modifier_llm",
            duration_ms=duration_ms,
            path_label=path_label,
        )
        if isinstance(result, ContextFrameReplayModifier):
            return result
        return cast(ContextFrameReplayModifier, ContextFrameReplayModifier.model_validate(result))

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        """Classify a user turn as a semantic edit to pending confirmation tasks."""
        user_prompt = PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = PENDING_ACTION_EDIT_SYSTEM_PROMPT
        start = time.perf_counter()
        result = await self.structured_pending_action_edit.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "pending_action_edit_llm_call",
            duration_ms=round(duration_ms, 2),
            model=self._model_name(self.interrupt_llm),
            system_chars=len(system_prompt),
            user_chars=len(user_prompt),
            context_chars=len(context),
            context_mode="compact" if context == "None" else "full",
        )
        self._log_latency_span(span="pending_action_edit_llm", duration_ms=duration_ms, path_label=path_label)
        if isinstance(result, PendingActionEditDecision):
            return result
        return cast(PendingActionEditDecision, PendingActionEditDecision.model_validate(result))

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
    "CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT",
    "TaskPlanner",
    "SEMANTIC_ROUTER_SYSTEM_PROMPT",
    "build_planner_system_prompt",
    "refresh_planner_system_prompt",
    "OrchestratorTaskPlanner",
]
