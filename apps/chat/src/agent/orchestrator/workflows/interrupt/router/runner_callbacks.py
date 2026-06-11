from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.auth.auth_resolve import _approve_auth_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import (
    _approve_confirmation_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_reprompt import _reprompt_or_reset_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_callbacks import (
    _callback_authorization_matches_interrupt,
    _callback_flow_matches_interrupt,
    _is_verified_pin_callback,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from shared.utils.logging import log_fingerprint


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
    auth_matches, callback_idempotency_key, interrupt_keys = _callback_authorization_matches_interrupt(
        state,
        interrupt,
    )
    if not auth_matches:
        logger.warning(
            "pin_callback_authorization_binding_mismatch",
            kind=interrupt.kind,
            callback_key_hash=log_fingerprint(callback_idempotency_key),
            interrupt_key_hashes=[log_fingerprint(key) for key in interrupt_keys],
            tasks=interrupt.task_ids,
        )
        updates = await _reprompt_or_reset_updates(state, interrupt, runtime.redis_client)
        updates["pin_verified"] = False
        updates["authorization_context"] = None
        return updates
    authorization_context = state.authorization_context
    if authorization_context is None:
        logger.warning(
            "pin_callback_authorization_binding_mismatch",
            kind=interrupt.kind,
            callback_key_hash=log_fingerprint(callback_idempotency_key),
            interrupt_key_hashes=[log_fingerprint(key) for key in interrupt_keys],
            reason="missing_authorization_context_after_match",
            tasks=interrupt.task_ids,
        )
        updates = await _reprompt_or_reset_updates(state, interrupt, runtime.redis_client)
        updates["pin_verified"] = False
        updates["authorization_context"] = None
        return updates
    bound_authorization_context = authorization_context.model_copy(
        update={"authorized_task_idempotency_keys": interrupt_keys}
    )
    authorized_state = state.model_copy(update={"authorization_context": bound_authorization_context})
    if interrupt.kind == "confirmation":
        updates = _approve_confirmation_updates(authorized_state, interrupt)
    else:
        updates = _approve_auth_updates(authorized_state, interrupt)
    updates["authorization_context"] = bound_authorization_context
    return updates


__all__ = ["_verified_pin_callback_updates"]
