"""Task-plan response assembly for the planner runner."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.node_updates import _planner_route_updates
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_unsupported import _build_policy_notice
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_task_policy_notice(
    *,
    text: str,
    planner_output: Any,
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
    planner_output: Any,
    text: str,
    current_locale: str,
    locale_updates: dict[str, Any],
    state_view: PlannerStateView,
) -> dict[str, Any]:
    if task_updates.get("capability_block_response"):
        return {
            "final_response": task_updates["capability_block_response"],
            "normalized_instruction": text,
            "planner_output": planner_output,
            "semantic_path_shape": "planner_capability_blocked",
            **_planner_route_updates(decision="capability_blocked", planner_output=planner_output),
            **locale_updates,
        }
    if task_updates.get("batch_limit_response"):
        return {
            "final_response": task_updates["batch_limit_response"],
            "normalized_instruction": text,
            "planner_output": planner_output,
            "semantic_path_shape": "planner",
            **_planner_route_updates(decision="transaction_batch_limit", planner_output=planner_output),
            **locale_updates,
        }

    stashed_query_session_update = task_updates["stashed_query_session_update"]
    return {
        "tasks": task_updates["new_tasks"],
        "waves": task_updates["waves"],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
        "policy_notice": _resolve_task_policy_notice(
            text=text,
            planner_output=planner_output,
            current_locale=current_locale,
            task_updates=task_updates,
        ),
        "semantic_path_shape": "planner",
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state_view.stashed_query_session
        ),
        **_planner_route_updates(
            decision=str(getattr(planner_output, "primary_intent", "") or "planner_task_plan"),
            planner_output=planner_output,
        ),
        **locale_updates,
    }


__all__ = ["_build_planner_task_response"]
