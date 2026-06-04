"""Standard planner-owned outcome builders."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.node_updates import _planner_route_updates
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.bridge import render_safe_capability_fallback
from shared.types.planner import PlannerOutput

# Planner updates are heterogeneous LangGraph state patches containing task maps,
# waves, Pydantic planner outputs, localized responses, and route metadata.
PlannerUpdates = dict[str, Any]


def unavailable_response(current_locale: str) -> PlannerUpdates:
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **_planner_route_updates(decision="planner_unavailable"),
    }


def quoted_replay_dispatch(quoted_replay_updates: PlannerUpdates) -> PlannerUpdates:
    return {
        **quoted_replay_updates,
        **_planner_route_updates(decision="quoted_replay"),
    }


def context_read_shortcut(shortcut_updates: PlannerUpdates) -> PlannerUpdates:
    return {
        **shortcut_updates,
        "semantic_path_shape": shortcut_updates.get("semantic_path_shape") or "planner",
        **_planner_route_updates(decision="planner_context_read"),
    }


def failure_response() -> PlannerUpdates:
    return _planner_route_updates(decision="planner_failed")


def non_task_response(
    *,
    handled_response: PlannerUpdates,
    planner_output: PlannerOutput,
) -> PlannerUpdates:
    return {
        **handled_response,
        "semantic_path_shape": handled_response.get("semantic_path_shape") or "planner",
        **_planner_route_updates(
            decision=planner_output.primary_intent or "planner_non_task_response",
            planner_output=planner_output,
        ),
    }


def policy_block(
    *,
    response: str,
    normalized_instruction: str,
    planner_output: PlannerOutput,
    locale_updates: PlannerUpdates,
) -> PlannerUpdates:
    return {
        "final_response": response,
        "normalized_instruction": normalized_instruction,
        "planner_output": planner_output,
        "semantic_path_shape": "planner_capability_blocked",
        **_planner_route_updates(decision="capability_blocked", planner_output=planner_output),
        **locale_updates,
    }


def batch_limit_response(
    *,
    response: str,
    normalized_instruction: str,
    planner_output: PlannerOutput,
    locale_updates: PlannerUpdates,
) -> PlannerUpdates:
    return {
        "final_response": response,
        "normalized_instruction": normalized_instruction,
        "planner_output": planner_output,
        "semantic_path_shape": "planner",
        **_planner_route_updates(decision="transaction_batch_limit", planner_output=planner_output),
        **locale_updates,
    }


def task_dispatch(
    *,
    task_updates: PlannerUpdates,
    planner_output: PlannerOutput,
    normalized_instruction: str,
    policy_notice: str | None,
    locale_updates: PlannerUpdates,
    state_view: PlannerStateView,
) -> PlannerUpdates:
    stashed_query_session_update = task_updates["stashed_query_session_update"]
    return {
        "tasks": task_updates["new_tasks"],
        "waves": task_updates["waves"],
        "current_wave_index": 0,
        "normalized_instruction": normalized_instruction,
        "planner_output": planner_output,
        "policy_notice": policy_notice,
        "semantic_path_shape": "planner",
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state_view.stashed_query_session
        ),
        **_planner_route_updates(
            decision=planner_output.primary_intent or "planner_task_plan",
            planner_output=planner_output,
        ),
        **locale_updates,
    }


__all__ = [
    "PlannerUpdates",
    "batch_limit_response",
    "context_read_shortcut",
    "failure_response",
    "non_task_response",
    "policy_block",
    "quoted_replay_dispatch",
    "task_dispatch",
    "unavailable_response",
]
