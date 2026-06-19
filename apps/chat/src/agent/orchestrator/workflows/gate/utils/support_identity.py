"""Support identity selection for gate shortcuts."""

from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView


def _recent_batch_identity(state_view: GateStateView) -> str | None:
    for value in (state_view.channel_identity, state_view.phone_number, state_view.user_id):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _support_user_id(state_view: GateStateView) -> str:
    loaded_user_id = state_view.loaded_context_or_empty.get("user_id")
    for value in (loaded_user_id, state_view.user_id, state_view.phone_number):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
