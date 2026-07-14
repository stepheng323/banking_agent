"""Standard gate outcome builders for common routing update shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    TurnNextStep,
    TurnOutcomeKind,
    route_resolution,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext


def planner_handoff(
    ctx: GateContext,
    *,
    owner: str = "guardrail",
    decision: str = "planner_handoff",
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    source: str = "planner_fallback",
    path_shape: str = "planner_handoff",
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> RouteResolution:
    updates = {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **dict(extra_updates or {}),
    }
    return route_resolution(
        updates=updates,
        owner=owner,  # type: ignore[arg-type]
        decision=decision,
        outcome_kind=TurnOutcomeKind.PLANNER_HANDOFF,
        next_step=TurnNextStep.PLAN,
        target_domain=target_domain,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source=source,
        path_shape=path_shape,
        heuristic_type=heuristic_type,
        heuristic_name=heuristic_name,
    )


def direct_response(
    ctx: GateContext,
    *,
    response: str,
    owner: str,
    decision: str,
    path_shape: str,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> RouteResolution:
    updates = {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "final_response": response,
    }
    return route_resolution(
        updates=updates,
        owner=owner,  # type: ignore[arg-type]
        decision=decision,
        outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
        next_step=TurnNextStep.FINALIZE,
        target_domain=target_domain,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source=source,
        path_shape=path_shape,
        heuristic_type=heuristic_type,
        heuristic_name=heuristic_name,
    )


def task_dispatch(
    ctx: GateContext,
    *,
    tasks: Mapping[str, object],
    waves: Sequence[Sequence[str]],
    owner: str,
    decision: str,
    path_shape: str | None = None,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> RouteResolution:
    updates: dict[str, object] = {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "tasks": dict(tasks),
        "waves": [list(wave) for wave in waves],
        "current_wave_index": 0,
        "planner_output": None,
    }
    path_shape = path_shape or source or decision
    return route_resolution(
        updates=updates,
        owner=owner,  # type: ignore[arg-type]
        decision=decision,
        outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
        next_step=TurnNextStep.ADVANCE,
        target_domain=target_domain,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source=source,
        path_shape=path_shape,
        heuristic_type=heuristic_type,
        heuristic_name=heuristic_name,
    )


def policy_block(
    ctx: GateContext,
    *,
    response: str,
    decision: str,
    path_shape: str,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    owner: str = "guardrail",
    mode: str | None = "new",
    source: str = "capability_guard",
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> RouteResolution:
    updates = {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "final_response": response,
    }
    return route_resolution(
        updates=updates,
        owner=owner,  # type: ignore[arg-type]
        decision=decision,
        outcome_kind=TurnOutcomeKind.POLICY_BLOCK,
        next_step=TurnNextStep.FINALIZE,
        target_domain=target_domain,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        source=source,
        path_shape=path_shape,
        heuristic_type=heuristic_type,
        heuristic_name=heuristic_name,
    )


def interrupt_handoff(
    ctx: GateContext,
    *,
    pending_interrupt: object,
    owner: str = "guardrail",
    decision: str = "interrupt_handoff",
    extra_updates: Mapping[str, object] | None = None,
    source: str = "gate_interrupt_fallback",
    path_shape: str = "interrupt_handoff",
) -> RouteResolution:
    updates = {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "pending_interrupt": pending_interrupt,
    }
    return route_resolution(
        updates=updates,
        owner=owner,  # type: ignore[arg-type]
        decision=decision,
        outcome_kind=TurnOutcomeKind.INTERRUPT_HANDOFF,
        next_step=TurnNextStep.HANDLE_INTERRUPT,
        source=source,
        path_shape=path_shape,
    )


__all__ = [
    "direct_response",
    "interrupt_handoff",
    "planner_handoff",
    "policy_block",
    "task_dispatch",
]
