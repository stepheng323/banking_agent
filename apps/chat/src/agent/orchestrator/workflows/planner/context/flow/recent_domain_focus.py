"""Derive recent planner-domain focus from authoritative tasks and result frames."""

from typing import cast

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView


def infer_recent_domain_focus(state_view: PlannerStateView) -> str | None:
    prior_output = state_view.planner_output
    if prior_output:
        prior_tasks = getattr(prior_output, "tasks", None) or []
        prior_executors = {
            getattr(task, "executor", None)
            for task in prior_tasks
            if getattr(task, "executor", None)
        }
        if len(prior_executors) == 1:
            return cast(str, next(iter(prior_executors)))

    frame_domains = {
        ContextFrameType.ACCOUNT_LIST: "account",
        ContextFrameType.BENEFICIARY_LIST: "beneficiary",
        ContextFrameType.SCHEDULE_LIST: "schedule",
        ContextFrameType.TRANSACTION_LIST: "query",
        ContextFrameType.RECEIPT: "support",
    }
    for frame in reversed(state_view.context_frames):
        domain = frame_domains.get(frame.frame_type)
        if domain is not None:
            return domain

    return state_view.current_wave_first_task_type


__all__ = ["infer_recent_domain_focus"]
