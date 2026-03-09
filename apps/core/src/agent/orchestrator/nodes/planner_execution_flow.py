"""Planner execution and retry flow helpers."""

from dataclasses import dataclass
from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import PLANNER_CONTEXT_MAX_CHARS, _clip_text
from apps.core.src.agent.orchestrator.nodes.planner_fastpath import (
    CONTEXT_FASTPATH_FLOW_SUBTYPES,
    NO_ACTIVE_FLOW_FASTPATH_MESSAGE,
    TRANSACTION_EXECUTORS,
    _build_beneficiary_fastpath_context_updates,
    _build_fastpath_fallback_task,
    _context_fastpath_shown_limit,
    _context_fastpath_total_items,
    _has_context_for_fastpath_subtype,
    _planner_fastpath_subtype,
)
from apps.core.src.agent.orchestrator.nodes.planner_guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from shared.i18n import LanguageDetectionSignal, LocaleManager
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
    active_intent: str | None,
    current_locale: str,
    redis_client: Any | None,
) -> PlannerExecutionResult:
    planner_output = await task_planner.plan_tasks(state.phone_number, text, context=planner_context)
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
            if not has_context_for_fastpath:
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


async def _retry_expected_executors_if_needed(
    *,
    state: OrchestratorState,
    task_planner: Any,
    planner_output: Any,
    text: str,
    planner_context: str,
    active_intent: str | None,
    current_locale: str,
) -> Any:
    expected_executors = {
        str(item)
        for item in (state.preplanner_expected_transaction_executors or [])
        if str(item) in TRANSACTION_EXECUTORS
    }
    if not expected_executors:
        return planner_output

    planned_executors = {task.executor for task in planner_output.tasks if task.executor in TRANSACTION_EXECUTORS}
    missing_executors = sorted(expected_executors - planned_executors)
    if not missing_executors:
        return planner_output

    retry_context = _clip_text(
        (
            f"{planner_context}\n\nPre-planner expected explicit transaction executors: "
            f"{', '.join(sorted(expected_executors))}. "
            f"Ensure all explicit executors are represented in tasks."
        ),
        PLANNER_CONTEXT_MAX_CHARS,
    )
    try:
        retry_output = await task_planner.plan_tasks(state.phone_number, text, context=retry_context)
        retry_output = _filter_spurious_affirmation_tasks(
            retry_output,
            active_intent=active_intent,
            pending_interrupt_kind=state.pending_interrupt.kind if state.pending_interrupt else None,
        )
        retry_output = _deescalate_mandate_acknowledgement(
            retry_output,
            loaded_context=state.loaded_context,
            locale=current_locale,
        )
        retry_executors = {task.executor for task in retry_output.tasks if task.executor in TRANSACTION_EXECUTORS}
        if expected_executors.issubset(retry_executors):
            logger.info(
                "planner_expected_executor_retry_applied",
                expected_executors=sorted(expected_executors),
                missing_executors=missing_executors,
            )
            return retry_output
        logger.warning(
            "planner_expected_executor_retry_rejected",
            expected_executors=sorted(expected_executors),
            retry_executors=sorted(retry_executors),
        )
    except Exception as exc:
        logger.warning(
            "planner_expected_executor_retry_failed",
            error=str(exc),
            expected_executors=sorted(expected_executors),
            missing_executors=missing_executors,
        )
    return planner_output


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
    "_retry_expected_executors_if_needed",
]
