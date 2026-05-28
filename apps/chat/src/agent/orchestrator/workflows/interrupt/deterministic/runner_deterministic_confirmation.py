"""Deterministic confirmation interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import resolve_shortcut_locale
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_repeat import (
    _resolve_deterministic_confirmation_repeat_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime


def _confirmation_repeat_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    route = _resolve_deterministic_confirmation_repeat_route(
        state=state,
        interrupt=runtime.interrupt,
        text=runtime.text,
    )
    if route is None:
        return None

    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    logger.info(
        "interrupt_shortcut_hit",
        kind=runtime.interrupt.kind,
        decision=route.decision,
        reason=route.reason,
        status_query_type=route.status_query_type,
        locale=shortcut_locale.value if shortcut_locale else None,
    )
    return _continue_flow_updates(state, runtime.interrupt)


__all__ = ["_confirmation_repeat_updates"]
