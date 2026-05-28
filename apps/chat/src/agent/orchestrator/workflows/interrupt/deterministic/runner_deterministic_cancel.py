"""Deterministic cancel interrupt shortcuts."""

from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.cancellation import cancel_match_kind, is_obvious_cancel_message
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _cancel_updates, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime


async def _deterministic_cancel_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    deterministic_cancel_kind = cancel_match_kind(runtime.text) if is_obvious_cancel_message(runtime.text) else None
    if deterministic_cancel_kind is None:
        return None
    logger.info(
        "interrupt_deterministic_cancel_hit",
        kind=runtime.interrupt.kind,
        match_kind=deterministic_cancel_kind,
    )
    return await _cancel_updates(state, runtime.interrupt, runtime.redis_client)


__all__ = ["_deterministic_cancel_updates"]
