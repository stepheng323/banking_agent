"""Canonical routing outcome for the interrupt workflow boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    RoutingContractError,
    TurnNextStep,
    TurnOutcomeKind,
    route_resolution,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import RouterDomainIntent


def _post_update_value(updates: Mapping[str, Any], key: str, current: Any) -> Any:
    return updates.get(key, current)


def _has_active_wave(state: OrchestratorState, updates: Mapping[str, Any]) -> bool:
    waves = _post_update_value(updates, "waves", state.waves)
    index = _post_update_value(updates, "current_wave_index", state.current_wave_index)
    return isinstance(waves, list) and isinstance(index, int) and 0 <= index < len(waves)


def _ensure_runnable_wave(state: OrchestratorState, updates: dict[str, Any]) -> None:
    if _has_active_wave(state, updates):
        return
    if _post_update_value(updates, "pending_interrupt", state.pending_interrupt) is not None:
        return
    tasks = updates.get("tasks")
    if not isinstance(tasks, Mapping) or not tasks:
        return
    terminal_stages = {"completed", "failed", "cancelled"}
    task_ids = [
        str(task_id)
        for task_id, task in tasks.items()
        if str(getattr(task, "stage", "")).lower().rsplit(".", maxsplit=1)[-1] not in terminal_stages
    ]
    if task_ids:
        updates["waves"] = [task_ids]
        updates["current_wave_index"] = 0


def _ensure_visible_wait_response(state: OrchestratorState, updates: dict[str, Any]) -> None:
    if updates.get("final_response") or updates.get("outbox"):
        return
    pending = _post_update_value(updates, "pending_interrupt", state.pending_interrupt)
    if pending is None:
        return
    prompt = str(getattr(pending, "prompt", "") or "").strip()
    if not prompt:
        prompt = render_message(
            "conversational.clarify",
            interrupt_state_view(state).current_locale,
        )
    updates["outbox"] = [{"type": "say", "text": prompt}]


@dataclass(frozen=True, slots=True)
class InterruptResolution:
    """Typed interrupt decision committed at the workflow boundary."""

    state: OrchestratorState
    route: RouteResolution

    def materialize(self) -> dict[str, Any]:
        return self.route.materialize(base_state=self.state)


def resolve_interrupt_updates(
    state: OrchestratorState,
    updates: Mapping[str, Any],
    *,
    decision: str | None = None,
    source: str = "interrupt",
    target_domain: RouterDomainIntent | None = None,
) -> InterruptResolution:
    """Convert one interrupt branch into a typed, validated route resolution.

    Leaf handlers remain focused on domain state mutation. The interrupt
    workflow must convert their patch to this contract before returning to the
    graph, so graph routing never inspects the patch itself.
    """
    committed = dict(updates)
    forbidden = {"direct_path_triggered", "semantic_path_shape", "turn_directive"}.intersection(
        committed
    )
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise RoutingContractError(
            f"interrupt payload overrides controlled routing fields: {names}"
        )
    _ensure_runnable_wave(state, committed)
    _ensure_visible_wait_response(state, committed)

    final_response = committed.get("final_response")
    pending_interrupt = _post_update_value(committed, "pending_interrupt", state.pending_interrupt)
    path_shape = str(committed.pop("path_shape", "") or "").strip()

    if final_response:
        outcome_kind = TurnOutcomeKind.DIRECT_RESPONSE
        next_step = TurnNextStep.END
        fallback_decision = "interrupt_final_response"
    elif pending_interrupt is not None:
        outcome_kind = TurnOutcomeKind.DIRECT_RESPONSE
        next_step = TurnNextStep.END
        fallback_decision = "interrupt_waiting"
    elif _has_active_wave(state, committed):
        outcome_kind = TurnOutcomeKind.TASK_DISPATCH
        next_step = TurnNextStep.ADVANCE
        fallback_decision = "interrupt_resolved"
    else:
        outcome_kind = TurnOutcomeKind.PLANNER_HANDOFF
        next_step = TurnNextStep.PLAN
        fallback_decision = "interrupt_replan"

    resolved_decision = decision or path_shape or fallback_decision
    resolved_path_shape = path_shape or resolved_decision
    route = route_resolution(
        updates=committed,
        owner="interrupt",
        decision=resolved_decision,
        outcome_kind=outcome_kind,
        next_step=next_step,
        source=source,
        path_shape=resolved_path_shape,
        target_domain=target_domain,
    )
    return InterruptResolution(state=state, route=route)


def commit_interrupt_outcome(
    state: OrchestratorState,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility helper for focused leaf tests; runtime uses the typed result."""

    return resolve_interrupt_updates(state, updates).materialize()


__all__ = ["InterruptResolution", "commit_interrupt_outcome", "resolve_interrupt_updates"]
