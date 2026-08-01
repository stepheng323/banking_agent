"""Standard planner-owned outcome builders."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution, TurnOutcomeKind
from apps.chat.src.agent.orchestrator.workflows.planner.node_updates import _planner_route_updates
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.bridge import render_safe_capability_fallback
from shared.types.planner import PlannerOutput

PlannerUpdates = dict[str, Any]
PlannerResolution = RouteResolution


def _shortcut_outcome(updates: PlannerUpdates) -> TurnOutcomeKind:
    waves = updates.get("waves")
    index = updates.get("current_wave_index", 0)
    if isinstance(waves, list) and isinstance(index, int) and 0 <= index < len(waves) and waves[index]:
        return TurnOutcomeKind.TASK_DISPATCH
    if updates.get("final_response") or updates.get("outbox"):
        return TurnOutcomeKind.DIRECT_RESPONSE
    raise ValueError("planner shortcut must contain a visible response or runnable wave")


def unavailable_response(current_locale: str) -> PlannerResolution:
    return _planner_route_updates(
        updates={
            "final_response": render_safe_capability_fallback(current_locale),
        },
        decision="planner_unavailable",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
    )


def quoted_replay_dispatch(quoted_replay_updates: PlannerUpdates) -> PlannerResolution:
    return _planner_route_updates(
        updates=quoted_replay_updates,
        decision="quoted_replay",
        outcome_kind=_shortcut_outcome(quoted_replay_updates),
    )


def context_read_shortcut(shortcut_updates: PlannerUpdates) -> PlannerResolution:
    return _planner_route_updates(
        updates={
            **shortcut_updates,
            "path_shape": shortcut_updates.get("path_shape") or "planner",
        },
        decision="planner_context_read",
        outcome_kind=_shortcut_outcome(shortcut_updates),
    )


def failure_response(current_locale: str = "en") -> PlannerResolution:
    return _planner_route_updates(
        updates={"final_response": render_safe_capability_fallback(current_locale)},
        decision="planner_failed",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
    )


def non_task_response(
    *,
    handled_response: PlannerUpdates,
    planner_output: PlannerOutput,
) -> PlannerResolution:
    return _planner_route_updates(
        updates={
            **handled_response,
            "path_shape": handled_response.get("path_shape") or "planner",
        },
        decision=planner_output.primary_intent or "planner_non_task_response",
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        planner_output=planner_output,
    )


def policy_block(
    *,
    response: str,
    normalized_instruction: str,
    planner_output: PlannerOutput,
    locale_updates: PlannerUpdates,
) -> PlannerResolution:
    return _planner_route_updates(
        updates={
            "final_response": response,
            "normalized_instruction": normalized_instruction,
            "planner_output": planner_output,
            "path_shape": "planner_capability_blocked",
            **locale_updates,
        },
        decision="capability_blocked",
        outcome_kind=TurnOutcomeKind.POLICY_BLOCK,
        planner_output=planner_output,
    )


def batch_limit_response(
    *,
    response: str,
    normalized_instruction: str,
    planner_output: PlannerOutput,
    locale_updates: PlannerUpdates,
) -> PlannerResolution:
    return _planner_route_updates(
        updates={
            "final_response": response,
            "normalized_instruction": normalized_instruction,
            "planner_output": planner_output,
            "path_shape": "planner",
            **locale_updates,
        },
        decision="transaction_batch_limit",
        outcome_kind=TurnOutcomeKind.POLICY_BLOCK,
        planner_output=planner_output,
    )


def task_dispatch(
    *,
    task_updates: PlannerUpdates,
    planner_output: PlannerOutput,
    normalized_instruction: str,
    policy_notice: str | None,
    locale_updates: PlannerUpdates,
    state_view: PlannerStateView,
) -> PlannerResolution:
    pending_query_clarification_update = task_updates.get("pending_query_clarification_update")
    return _planner_route_updates(
        updates={
            "tasks": task_updates.get("new_tasks", {}),
            "waves": task_updates.get("waves", []),
            "current_wave_index": 0,
            "normalized_instruction": normalized_instruction,
            "planner_output": planner_output,
            "policy_notice": policy_notice,
            "path_shape": "planner",
            "pending_query_clarification": (
                pending_query_clarification_update
                if pending_query_clarification_update
                else state_view.pending_query_clarification
            ),
            **locale_updates,
        },
        decision=planner_output.primary_intent or "planner_task_plan",
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        planner_output=planner_output,
    )


__all__ = [
    "PlannerUpdates",
    "PlannerResolution",
    "batch_limit_response",
    "context_read_shortcut",
    "failure_response",
    "non_task_response",
    "policy_block",
    "quoted_replay_dispatch",
    "task_dispatch",
    "unavailable_response",
]
