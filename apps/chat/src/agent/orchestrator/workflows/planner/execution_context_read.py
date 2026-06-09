"""Context-read post-processing for planner execution."""

from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_account import (
    synthesize_context_read_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_availability import (
    _context_read_shown_limit,
    _context_read_total_items,
    _has_context_for_read_subtype,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    CONTEXT_READ_ACCOUNT_SUBTYPES,
    CONTEXT_READ_FLOW_SUBTYPES,
    NO_ACTIVE_FLOW_CONTEXT_READ_MESSAGE,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_fallback import (
    _build_context_read_fallback_task,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_focus import (
    _planner_context_read_subtype,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_ACCOUNT_CONTEXT_READ_NON_OVERRIDE_ACTIONS = {"none", "unknown", "list", "list_accounts", "count"}


def _apply_context_read_planner_shape(
    *,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    text: str,
    current_locale: str,
) -> str | None:
    context_read_subtype = _planner_context_read_subtype(planner_output)
    if not context_read_subtype:
        return None

    has_context_for_read = _has_context_for_read_subtype(state_view, context_read_subtype)
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
        _apply_context_read_direct_response(
            state_view=state_view,
            planner_output=planner_output,
            context_read_subtype=context_read_subtype,
            text=text,
            current_locale=current_locale,
        )
        return context_read_subtype

    _apply_context_read_fallback(
        planner_output=planner_output,
        context_read_subtype=context_read_subtype,
        text=text,
        has_context_for_read=has_context_for_read,
        has_no_tasks=has_no_tasks,
        is_flow_context_read=is_flow_context_read,
        account_fallback_action=account_fallback_action,
    )
    return context_read_subtype


def _apply_context_read_direct_response(
    *,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    context_read_subtype: str,
    text: str,
    current_locale: str,
) -> None:
    logger.info("context_read_hit", subtype=context_read_subtype)
    synthesized_response = synthesize_context_read_response(
        state_view,
        context_read_subtype,
        text,
        current_locale,
    )
    if synthesized_response:
        planner_output.response = synthesized_response
    total_items = _context_read_total_items(state_view, context_read_subtype)
    shown_limit = _context_read_shown_limit(context_read_subtype)
    if total_items is not None and total_items > shown_limit:
        logger.info(
            "context_read_list_truncated",
            subtype=context_read_subtype,
            shown=shown_limit,
            total=total_items,
        )


def _apply_context_read_fallback(
    *,
    planner_output: PlannerOutput,
    context_read_subtype: str,
    text: str,
    has_context_for_read: bool,
    has_no_tasks: bool,
    is_flow_context_read: bool,
    account_fallback_action: str | None,
) -> None:
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


__all__ = ["_apply_context_read_planner_shape"]
