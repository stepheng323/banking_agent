"""Planner execution flow helpers."""

from dataclasses import dataclass
from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import (
    CONTEXT_FASTPATH_FLOW_SUBTYPES,
    NO_ACTIVE_FLOW_FASTPATH_MESSAGE,
    _build_beneficiary_fastpath_context_updates,
    _build_fastpath_fallback_task,
    _context_fastpath_shown_limit,
    _context_fastpath_total_items,
    _has_context_for_fastpath_subtype,
    _planner_fastpath_subtype,
    _should_bypass_account_read_fastpath,
)
from apps.core.src.agent.orchestrator.nodes.planner_guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from shared.i18n import LanguageDetectionSignal, LocaleManager
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class PlannerExecutionResult:
    planner_output: Any
    current_locale: str
    fastpath_context_updates: dict[str, Any]


async def _execute_planner_with_context(
    *,
    state: OrchestratorState,
    task_planner: Any,
    text: str,
    planner_context: str,
    prompt_signals: PlannerPromptSignals,
    active_intent: str | None,
    current_locale: str,
    redis_client: Any | None,
) -> PlannerExecutionResult:
    planner_output = await task_planner.plan_tasks(
        state.phone_number,
        text,
        context=planner_context,
        prompt_signals=prompt_signals,
    )
    planner_output = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent=active_intent,
        pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
    )
    planner_output = _deescalate_mandate_acknowledgement(
        planner_output,
        loaded_context=state.loaded_context,
        locale=current_locale,
    )
    logger.info("planner_tasks_generated", output=planner_output)

    fastpath_subtype = _planner_fastpath_subtype(planner_output)
    if fastpath_subtype:
        has_context_for_fastpath = _has_context_for_fastpath_subtype(state, fastpath_subtype)
        has_no_tasks = not planner_output.tasks
        is_conversational_no_task = planner_output.primary_intent == "conversational" and has_no_tasks
        is_flow_fastpath = fastpath_subtype in CONTEXT_FASTPATH_FLOW_SUBTYPES
        bypass_read_fastpath = _should_bypass_account_read_fastpath(fastpath_subtype, text)
        if bypass_read_fastpath:
            has_context_for_fastpath = False

        if is_conversational_no_task and has_context_for_fastpath:
            logger.info("context_fastpath_hit", subtype=fastpath_subtype)
            total_items = _context_fastpath_total_items(state, fastpath_subtype)
            shown_limit = _context_fastpath_shown_limit(fastpath_subtype)
            if total_items is not None and total_items > shown_limit:
                logger.info(
                    "context_fastpath_list_truncated",
                    subtype=fastpath_subtype,
                    shown=shown_limit,
                    total=total_items,
                )
        else:
            if bypass_read_fastpath:
                fallback_reason = "account_mutation_request"
            elif not has_context_for_fastpath:
                fallback_reason = "insufficient_context"
            elif has_no_tasks:
                fallback_reason = "invalid_no_task_shape"
            else:
                fallback_reason = "planner_emitted_task"

            if has_no_tasks:
                fallback_task = _build_fastpath_fallback_task(fastpath_subtype, text)
                if fallback_task:
                    planner_output.tasks = [fallback_task]
                    planner_output.primary_intent = fallback_task.executor
                    planner_output.is_complex = False
                    planner_output.response = ""
                    planner_output.response_key = None
                    logger.info(
                        "context_fastpath_fallback_to_worker",
                        subtype=fastpath_subtype,
                        reason=fallback_reason,
                    )
                elif is_flow_fastpath and not has_context_for_fastpath:
                    planner_output.primary_intent = "conversational"
                    planner_output.tasks = []
                    planner_output.is_complex = False
                    planner_output.response_key = None
                    if not planner_output.response:
                        planner_output.response = NO_ACTIVE_FLOW_FASTPATH_MESSAGE
                    logger.info("interrupt_status_query_no_active_flow", subtype=fastpath_subtype)
            else:
                logger.info(
                    "context_fastpath_fallback_to_worker",
                    subtype=fastpath_subtype,
                    reason=fallback_reason,
                )

    detected_language = getattr(planner_output, "detected_language", None)
    if detected_language:
        if redis_client:
            signal = LanguageDetectionSignal(
                locale=LocaleManager.from_detection(detected_language),
                confidence=float(getattr(planner_output, "confidence", 1.0) or 0.0),
                source="planner",
                explicit=False,
            )
            resolved_locale = await LocaleManager.update_locale(state.phone_number, signal)
            current_locale = resolved_locale.value
        else:
            current_locale = LocaleManager.from_detection(detected_language).value

    if redis_client and planner_context != "None" and planner_output and planner_output.tasks:
        is_saving = any(t.executor == "beneficiary" and t.action == "save_beneficiary" for t in planner_output.tasks)
        if not is_saving:
            suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
            await redis_client.delete(suggestion_key)
            logger.info("cleared_stale_beneficiary_context", phone=state.phone_number)

    fastpath_context_updates = _build_beneficiary_fastpath_context_updates(
        state,
        planner_output,
        fastpath_subtype,
    )
    return PlannerExecutionResult(
        planner_output=planner_output,
        current_locale=current_locale,
        fastpath_context_updates=fastpath_context_updates,
    )


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
]
