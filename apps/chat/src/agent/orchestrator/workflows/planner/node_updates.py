"""Planner runner route metadata helpers."""

from collections.abc import Mapping
from typing import Any

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    TurnNextStep,
    TurnOutcomeKind,
    route_resolution,
)
from shared.types.planner import PlannerOutput

_PLANNER_DOMAIN_TARGETS = {"query", "account", "support", "beneficiary", "transfer", "airtime", "data", "schedule"}


def _planner_route_updates(
    *,
    updates: Mapping[str, Any],
    decision: str,
    outcome_kind: TurnOutcomeKind,
    planner_output: PlannerOutput | None = None,
    target_domain: str | None = None,
) -> RouteResolution:
    payload = dict(updates)
    raw_path_shape = payload.pop("path_shape", None)
    path_shape = raw_path_shape if isinstance(raw_path_shape, str) and raw_path_shape else "planner"
    resolved_target = target_domain
    if resolved_target is None and planner_output is not None:
        primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
        if primary_intent in _PLANNER_DOMAIN_TARGETS:
            resolved_target = primary_intent
    return route_resolution(
        updates={**payload, "planner_used": True},
        owner="planner",
        decision=decision,
        outcome_kind=outcome_kind,
        target_domain=resolved_target,  # type: ignore[arg-type]
        next_step=(
            TurnNextStep.ADVANCE
            if outcome_kind == TurnOutcomeKind.TASK_DISPATCH
            else TurnNextStep.END
        ),
        path_shape=path_shape,
    )


__all__ = ["_planner_route_updates"]
