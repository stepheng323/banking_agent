"""Task planner for breaking down user requests into executable tasks."""

import time

from langchain_openai import ChatOpenAI

import shared.services.task_planner_context_frame_prompts as context_frame_prompts
import shared.services.task_planner_interrupt_prompts as interrupt_prompts
import shared.services.task_planner_prompt_models as prompt_models
import shared.services.task_planner_quoted_replay_prompts as quoted_replay_prompts
import shared.services.task_planner_semantic_router_prompts as semantic_router_prompts
from shared.services.confirmation_classifier import classify_confirmation_reply
from shared.services.confirmation_models import ConfirmationDecision, ConfirmationPromptKind
from shared.services.task_planner_model_wiring import build_task_planner_structured_outputs
from shared.services.task_planner_normalizer import normalize_planner_transaction_output
from shared.services.task_planner_observability import invoke_structured_prompt, log_latency_span, model_name
from shared.services.task_planner_prompt_runtime import (
    PLANNER_PROMPT_BASELINE_RESULT,
    build_runtime_planner_system_prompt,
)
from shared.services.task_queue.service import TaskQueueService
from shared.services.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
)
from shared.services.unsupported_capability_semantic import (
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
        structured_outputs = build_task_planner_structured_outputs(
            planner_llm=planner_llm,
            semantic_router_llm=self.semantic_router_llm,
            interrupt_llm=self.interrupt_llm,
        )
        self.structured_planner = structured_outputs.planner
        self.structured_semantic_router = structured_outputs.semantic_router
        self.structured_schedule_read_router = structured_outputs.schedule_read_router
        self.structured_interrupt_router = structured_outputs.interrupt_router
        self.structured_quoted_replay = structured_outputs.quoted_replay
        self.structured_context_frame_followup = structured_outputs.context_frame_followup
        self.structured_context_frame_replay_modifier = structured_outputs.context_frame_replay_modifier
        self.structured_pending_action_edit = structured_outputs.pending_action_edit
        self.structured_confirmation_decision = structured_outputs.confirmation_decision
        self.structured_unsupported_capability = structured_outputs.unsupported_capability
        self.structured_unsupported_boundary_turn = structured_outputs.unsupported_boundary_turn
        self.task_queue_service = task_queue_service
        if not self.uses_dedicated_interrupt_model:
            logger.warning("interrupt_router_model_not_dedicated", mode="planner_fallback")
        if not self.uses_dedicated_semantic_router_model:
            logger.warning("semantic_router_model_not_dedicated", mode="interrupt_or_planner_fallback")

    async def plan_tasks(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: prompt_models.PlannerPromptSignals,
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
        prompt_input = prompt_models.PlannerPromptBuildInput(text=text, context=context, signals=prompt_signals)
        prompt_result = build_runtime_planner_system_prompt(prompt_input)
        system_prompt = prompt_result.system_prompt
        result = await invoke_structured_prompt(
            self.structured_planner,
            PlannerOutput,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="planner_llm_call",
            model_llm=self.planner_llm,
            path_label=path_label,
            latency_span="planner_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if prompt_signals.compact_context else "full",
                "prompt_profile": prompt_result.profile,
                "prompt_bundles": list(prompt_result.selected_bundle_ids),
                "prompt_rule_count": len(prompt_result.selected_rule_ids),
                "baseline_runtime_system_chars": PLANNER_PROMPT_BASELINE_RESULT.char_count,
                "baseline_runtime_profile": PLANNER_PROMPT_BASELINE_RESULT.profile,
            },
        )
        return normalize_planner_transaction_output(result, text)

    async def route_semantic_turn(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "direct_path",
    ) -> SemanticRouteDecision:
        """Top-level semantic routing before planner-owned dispatch."""
        user_prompt = semantic_router_prompts.SEMANTIC_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = semantic_router_prompts.SEMANTIC_ROUTER_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_semantic_router,
            SemanticRouteDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="semantic_router_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="semantic_router_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
        )

    async def route_schedule_read_turn(
        self,
        phone_number: str,
        text: str,
        *,
        path_label: str = "direct_path",
    ) -> SemanticRouteDecision:
        """Small semantic classifier for read-only scheduled-transaction list/count turns."""
        user_prompt = semantic_router_prompts.SCHEDULE_READ_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
        )
        system_prompt = semantic_router_prompts.SCHEDULE_READ_ROUTER_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_schedule_read_router,
            SemanticRouteDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="schedule_read_router_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="schedule_read_router_llm",
        )

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
        user_prompt = interrupt_prompts.INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = (
            interrupt_prompts.INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT
            if prompt_mode == "compact"
            else interrupt_prompts.INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
        )
        return await invoke_structured_prompt(
            self.structured_interrupt_router,
            InterruptRouteDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="interrupt_router_llm_call",
            model_llm=self.interrupt_llm,
            path_label=path_label,
            latency_span="interrupt_router_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
                "prompt_mode": prompt_mode,
            },
        )

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
            model=model_name(self.interrupt_llm),
            action=result.action,
            source=result.source,
            confidence=result.confidence,
            prompt_kind=prompt_kind,
            context_chars=len(context),
        )
        log_latency_span(logger, span="confirmation_decision_llm", duration_ms=duration_ms, path_label=path_label)
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
            model=model_name(self.semantic_router_llm),
            action=result.action,
            capability_key=result.capability_key,
            confidence=result.confidence,
            context_chars=len(context),
        )
        log_latency_span(
            logger,
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
            model=model_name(self.semantic_router_llm),
            action=result.action,
            capability_key=result.capability_key,
            confidence=result.confidence,
            boundary_key=boundary_key,
            context_chars=len(context),
        )
        log_latency_span(
            logger,
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
        user_prompt = context_frame_prompts.CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = context_frame_prompts.CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_context_frame_followup,
            ContextFrameFollowupDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="context_frame_followup_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="context_frame_followup_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
        )

    async def extract_context_frame_replay_modifiers(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameReplayModifier:
        """Extract a strict edit patch for frame-backed transaction replay."""
        user_prompt = context_frame_prompts.CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = context_frame_prompts.CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_context_frame_replay_modifier,
            ContextFrameReplayModifier,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="context_frame_replay_modifier_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="context_frame_replay_modifier_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
        )

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        """Classify a user turn as a semantic edit to pending confirmation tasks."""
        user_prompt = interrupt_prompts.PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = interrupt_prompts.PENDING_ACTION_EDIT_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_pending_action_edit,
            PendingActionEditDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="pending_action_edit_llm_call",
            model_llm=self.interrupt_llm,
            path_label=path_label,
            latency_span="pending_action_edit_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
        )

    async def interpret_quoted_replay(
        self, phone_number: str, text: str, context: str = "None"
    ) -> QuotedReplayInterpretation:
        """Interpret a quoted follow-up turn for replay semantics."""
        user_prompt = quoted_replay_prompts.QUOTED_REPLAY_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = quoted_replay_prompts.QUOTED_REPLAY_SYSTEM_PROMPT
        parsed = await invoke_structured_prompt(
            self.structured_quoted_replay,
            QuotedReplayInterpretation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="quoted_replay_llm_call",
            model_llm=self.planner_llm,
        )
        logger.info(
            "quoted_replay_decision",
            decision=parsed.decision,
            confidence=parsed.confidence,
            detected_language=parsed.detected_language,
            tasks=len(parsed.tasks),
        )
        return parsed

__all__ = [
    "PLANNER_USER_PROMPT_TEMPLATE",
    "TaskPlanner",
]
