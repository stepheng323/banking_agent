"""Standard gate outcome builders for common routing update shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import GateUpdates
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates


def planner_handoff(ctx: GateContext) -> GateUpdates:
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **_route_observability_updates(owner="planner", decision="planner_handoff"),
    }


def direct_response(
    ctx: GateContext,
    *,
    response: str,
    owner: str,
    decision: str,
    semantic_path_shape: str,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    route_source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> GateUpdates:
    return {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "direct_path_triggered": True,
        "final_response": response,
        "semantic_path_shape": semantic_path_shape,
        **_route_observability_updates(
            owner=owner,
            decision=decision,
            target_domain=target_domain,
            mode=mode,
            route_source=route_source,
            heuristic_type=heuristic_type,
            heuristic_name=heuristic_name,
        ),
    }


def task_dispatch(
    ctx: GateContext,
    *,
    tasks: Mapping[str, object],
    waves: Sequence[Sequence[str]],
    owner: str,
    decision: str,
    semantic_path_shape: str | None = None,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    route_source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> GateUpdates:
    updates: GateUpdates = {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        "tasks": dict(tasks),
        "waves": [list(wave) for wave in waves],
        "current_wave_index": 0,
        "planner_output": None,
        "direct_path_triggered": True,
        **_route_observability_updates(
            owner=owner,
            decision=decision,
            target_domain=target_domain,
            mode=mode,
            route_source=route_source,
            heuristic_type=heuristic_type,
            heuristic_name=heuristic_name,
        ),
    }
    if semantic_path_shape is not None:
        updates["semantic_path_shape"] = semantic_path_shape
    return updates


def policy_block(
    ctx: GateContext,
    *,
    response: str,
    decision: str,
    semantic_path_shape: str,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
) -> GateUpdates:
    return direct_response(
        ctx,
        response=response,
        owner="guardrail",
        decision=decision,
        semantic_path_shape=semantic_path_shape,
        extra_updates=extra_updates,
        target_domain=target_domain,
        mode="new",
    )


def hint_only(
    ctx: GateContext,
    *,
    owner: str,
    decision: str,
    extra_updates: Mapping[str, object] | None = None,
    target_domain: str | None = None,
    mode: str | None = None,
    route_source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> GateUpdates:
    return {
        **ctx.gate_updates,
        **dict(extra_updates or {}),
        **_route_observability_updates(
            owner=owner,
            decision=decision,
            target_domain=target_domain,
            mode=mode,
            route_source=route_source,
            heuristic_type=heuristic_type,
            heuristic_name=heuristic_name,
        ),
    }


__all__ = [
    "direct_response",
    "hint_only",
    "planner_handoff",
    "policy_block",
    "task_dispatch",
]
