from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _callback_flow_type(state: OrchestratorState) -> str | None:
    callback = interrupt_state_view(state).last_callback or {}
    raw_flow_type = callback.get("flow_type")
    if not isinstance(raw_flow_type, str):
        return None
    flow_type = raw_flow_type.strip().lower()
    return flow_type or None


def _is_verified_pin_callback(state: OrchestratorState) -> bool:
    state_view = interrupt_state_view(state)
    callback = state_view.last_callback
    if not isinstance(callback, dict):
        return False
    return bool(callback.get("pin_verified")) and state_view.pin_verified


def _callback_flow_matches_interrupt(
    state: OrchestratorState,
    current_task_types: set[str],
) -> tuple[bool, str | None]:
    callback_flow_type = _callback_flow_type(state)
    if not callback_flow_type:
        return True, None
    if not current_task_types:
        return False, callback_flow_type
    return callback_flow_type in current_task_types, callback_flow_type


__all__ = [
    "_callback_flow_matches_interrupt",
    "_callback_flow_type",
    "_is_verified_pin_callback",
]
