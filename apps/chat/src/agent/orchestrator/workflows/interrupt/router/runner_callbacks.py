from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.auth.auth_resolve import _approve_auth_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import (
    _approve_confirmation_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import _reprompt_or_reset_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_callbacks import (
    _callback_flow_matches_interrupt,
    _is_verified_pin_callback,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime


async def _verified_pin_callback_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if not (_is_verified_pin_callback(state) and interrupt.kind in {"confirmation", "auth"}):
        return None

    flow_matches, callback_flow_type = _callback_flow_matches_interrupt(state, runtime.current_task_types)
    if not flow_matches:
        logger.warning(
            "interrupt_callback_flow_mismatch",
            kind=interrupt.kind,
            callback_flow_type=callback_flow_type,
            active_types=sorted(runtime.current_task_types),
            tasks=interrupt.task_ids,
        )
        return await _reprompt_or_reset_updates(state, interrupt, runtime.redis_client)
    if interrupt.kind == "confirmation":
        return _approve_confirmation_updates(state, interrupt)
    return _approve_auth_updates(state, interrupt)


__all__ = ["_verified_pin_callback_updates"]
