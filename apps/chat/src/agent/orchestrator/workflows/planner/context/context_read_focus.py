"""Subtype and recent-focus helpers for planner context reads."""

from typing import cast

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_constants import (
    CONTEXT_READ_ACCOUNT_SUBTYPES,
    CONTEXT_READ_BENEFICIARY_SUBTYPES,
    CONTEXT_READ_FLOW_SUBTYPES,
    CONTEXT_READ_SUBTYPES,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput


def _infer_recent_domain_focus(state_view: PlannerStateView) -> str | None:
    """Infer the most recent domain focus from prior planner output/state."""
    prior_output = state_view.planner_output
    if prior_output:
        prior_subtype = _planner_context_read_subtype(prior_output)
        if prior_subtype in CONTEXT_READ_ACCOUNT_SUBTYPES:
            return "account"
        if prior_subtype in CONTEXT_READ_BENEFICIARY_SUBTYPES:
            return "beneficiary"
        if prior_subtype in CONTEXT_READ_FLOW_SUBTYPES:
            return "orchestrator"

        prior_tasks = getattr(prior_output, "tasks", None) or []
        prior_executors = {getattr(task, "executor", None) for task in prior_tasks if getattr(task, "executor", None)}
        if len(prior_executors) == 1:
            return cast(str, next(iter(prior_executors)))

    current_task_type = state_view.current_wave_first_task_type
    if current_task_type:
        return current_task_type

    return None


def _planner_context_read_subtype(planner_output: PlannerOutput) -> str | None:
    """Read planner-provided context-read subtype when it is recognized."""
    subtype = getattr(planner_output, "context_read_subtype", None)
    if isinstance(subtype, str) and subtype in CONTEXT_READ_SUBTYPES:
        return subtype
    return None


__all__ = ["_infer_recent_domain_focus", "_planner_context_read_subtype"]
