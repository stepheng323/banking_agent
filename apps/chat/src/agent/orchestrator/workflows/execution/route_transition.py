"""Translate execution-wave state into the next canonical route disposition."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    RoutingContractError,
    TurnDirective,
    TurnNextStep,
    advance_directive,
)
from apps.chat.src.agent.orchestrator.workflows.execution.control_state import execution_control_state
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import wave_position


def _post_update_value(updates: Mapping[str, Any], key: str, current: Any) -> Any:
    return updates.get(key, current)


def _execution_next_step(state: OrchestratorState, updates: Mapping[str, Any]) -> TurnNextStep:
    control = execution_control_state(state)
    pending_interrupt = _post_update_value(updates, "pending_interrupt", control.pending_interrupt)
    if pending_interrupt is not None:
        return TurnNextStep.END

    position = wave_position(state)
    waves = _post_update_value(updates, "waves", position.waves)
    index = _post_update_value(updates, "current_wave_index", position.index)
    if isinstance(waves, list) and isinstance(index, int) and index < len(waves):
        return TurnNextStep.ADVANCE
    return TurnNextStep.FINALIZE


def translate_execution_route(
    state: OrchestratorState,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve the selected route identity while advancing its disposition."""
    translated = dict(updates)
    if "turn_directive" in translated:
        raise RoutingContractError("execution result cannot replace turn directive")
    next_step = _execution_next_step(state, translated)
    candidate = state.turn_directive
    if isinstance(candidate, TurnDirective):
        if candidate.next_step != TurnNextStep.ADVANCE:
            raise RoutingContractError("execution requires an advance directive")
        return RouteResolution(
            directive=advance_directive(candidate, next_step),
            updates=translated,
        ).materialize(base_state=state)

    raise RoutingContractError("execution wave requires an existing turn directive")


__all__ = ["translate_execution_route"]
