"""Planner execution flow helpers."""

from dataclasses import dataclass
from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner.context_read import (
    CONTEXT_READ_ACCOUNT_SUBTYPES,
    CONTEXT_READ_FLOW_SUBTYPES,
    NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE,
    _build_beneficiary_context_read_updates,
    _build_context_read_fallback_task,
    _context_read_shown_limit,
    _context_read_total_items,
    _has_context_for_read_subtype,
    _planner_context_read_subtype,
    synthesize_account_context_read_response,
)
from apps.core.src.agent.orchestrator.nodes.planner.guardrails import (
    _deescalate_mandate_acknowledgement,
    _enforce_beneficiary_routing_contract,
    _filter_spurious_affirmation_tasks,
)
from shared.i18n import LanguageDetectionSignal, LocaleManager
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_ACCOUNT_CONTEXT_READ_NON_OVERRIDE_ACTIONS = {"none", "unknown", "list", "list_accounts", "count"}


@dataclass(slots=True)
class PlannerExecutionResult:
    planner_output: Any
    current_locale: str
    context_read_updates: dict[str, Any]


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
    try:
        planner_output = await task_planner.plan_tasks(
            state.phone_number,
            text,
            context=planner_context,
            prompt_signals=prompt_signals,
            path_label="planner_path",
        )
    except TypeError:
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
    planner_output = _enforce_beneficiary_routing_contract(
        planner_output,
        message_id=state.last_message_id,
        user_text=text,
        has_beneficiary_suggestion=prompt_signals.has_beneficiary_suggestion,
    )
    logger.info("planner_tasks_generated", output=planner_output)

    context_read_subtype = _planner_context_read_subtype(planner_output)
    if context_read_subtype:
        has_context_for_read = _has_context_for_read_subtype(state, context_read_subtype)
        has_no_tasks = not planner_output.tasks
        is_conversational_no_task = planner_output.primary_intent == "conversational" and has_no_tasks
        is_flow_context_read = context_read_subtype in CONTEXT_READ_FLOW_SUBTYPES
        account_fallback_action: str | None = None

        if has_no_tasks and context_read_subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
            account_action_hint = str(getattr(planner_output, "account_action_hint", "none") or "none").strip().lower()
            if account_action_hint not in _ACCOUNT_CONTEXT_READ_NON_OVERRIDE_ACTIONS:
                has_context_for_read = False
                account_fallback_action = account_action_hint

        if is_conversational_no_task and has_context_for_read:
            logger.info("context_read_hit", subtype=context_read_subtype)
            synthesized_response = synthesize_account_context_read_response(
                state,
                context_read_subtype,
                text,
                current_locale,
            )
            if synthesized_response:
                planner_output.response = synthesized_response
            total_items = _context_read_total_items(state, context_read_subtype)
            shown_limit = _context_read_shown_limit(context_read_subtype)
            if total_items is not None and total_items > shown_limit:
                logger.info(
                    "context_read_list_truncated",
                    subtype=context_read_subtype,
                    shown=shown_limit,
                    total=total_items,
                )
        else:
            if account_fallback_action:
                fallback_reason = "account_action_override"
            elif not has_context_for_read:
                fallback_reason = "insufficient_context"
            elif has_no_tasks:
                fallback_reason = "invalid_no_task_shape"
            else:
                fallback_reason = "planner_emitted_task"

            if has_no_tasks:
                fallback_task = _build_context_read_fallback_task(
                    context_read_subtype,
                    text,
                    account_action_override=account_fallback_action,
                )
                if fallback_task:
                    planner_output.tasks = [fallback_task]
                    planner_output.primary_intent = fallback_task.executor
                    planner_output.is_complex = False
                    planner_output.response = ""
                    planner_output.response_key = None
                    logger.info(
                        "context_read_fallback_to_worker",
                        subtype=context_read_subtype,
                        reason=fallback_reason,
                    )
                elif is_flow_context_read and not has_context_for_read:
                    planner_output.primary_intent = "conversational"
                    planner_output.tasks = []
                    planner_output.is_complex = False
                    planner_output.response_key = None
                    if not planner_output.response:
                        planner_output.response = NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE
                    logger.info("interrupt_status_query_no_active_flow", subtype=context_read_subtype)
            else:
                logger.info(
                    "context_read_fallback_to_worker",
                    subtype=context_read_subtype,
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

    context_read_updates = _build_beneficiary_context_read_updates(
        state,
        planner_output,
        context_read_subtype,
    )
    return PlannerExecutionResult(
        planner_output=planner_output,
        current_locale=current_locale,
        context_read_updates=context_read_updates,
    )


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
]
