"""Canonical finalization transition helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    RoutingContractError,
    TurnNextStep,
    advance_directive,
)


def finalize_turn_route(state: OrchestratorState, updates: Mapping[str, Any]) -> dict[str, Any]:
    """End a finalized turn while preserving its route identity."""
    directive = state.turn_directive
    if directive is None:
        raise RoutingContractError("finalize requires an existing turn directive")
    if directive.next_step != TurnNextStep.FINALIZE:
        raise RoutingContractError("finalize requires a finalize directive")
    return RouteResolution(
        directive=advance_directive(directive, TurnNextStep.END),
        updates=dict(updates),
    ).materialize(base_state=state)


__all__ = ["finalize_turn_route"]
