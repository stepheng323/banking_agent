"""Task-plan response assembly for the planner runner."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.outcomes import (
    batch_limit_response,
    policy_block,
    task_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_unsupported import _build_policy_notice
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_task_policy_notice(
    *,
    text: str,
    planner_output: PlannerOutput,
    current_locale: str,
    task_updates: dict[str, Any],
) -> str | None:
    policy_notice = _build_policy_notice(text, planner_output, current_locale)
    capability_policy_notice = task_updates.get("capability_policy_notice")
    if policy_notice and capability_policy_notice:
        policy_notice = f"{capability_policy_notice}\n\n{policy_notice}"
    elif capability_policy_notice:
        policy_notice = capability_policy_notice
    if policy_notice:
        logger.info("policy_notice_created")
    return policy_notice


def _build_planner_task_response(
    *,
    task_updates: dict[str, Any],
    planner_output: PlannerOutput,
    text: str,
    current_locale: str,
    locale_updates: dict[str, Any],
    state_view: PlannerStateView,
) -> dict[str, Any]:
    if task_updates.get("capability_block_response"):
        return policy_block(
            response=task_updates["capability_block_response"],
            normalized_instruction=text,
            planner_output=planner_output,
            locale_updates=locale_updates,
        )
    if task_updates.get("batch_limit_response"):
        return batch_limit_response(
            response=task_updates["batch_limit_response"],
            normalized_instruction=text,
            planner_output=planner_output,
            locale_updates=locale_updates,
        )

    return task_dispatch(
        task_updates=task_updates,
        planner_output=planner_output,
        normalized_instruction=text,
        policy_notice=_resolve_task_policy_notice(
            text=text,
            planner_output=planner_output,
            current_locale=current_locale,
            task_updates=task_updates,
        ),
        locale_updates=locale_updates,
        state_view=state_view,
    )


__all__ = ["_build_planner_task_response"]
