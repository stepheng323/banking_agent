"""Support identity selection for gate shortcuts."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _recent_batch_identity_for_state(state: OrchestratorState) -> str | None:
    for value in (state.channel_identity, state.phone_number, state.user_id):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _support_user_id_for_state(state: OrchestratorState) -> str:
    loaded_user_id = (state.loaded_context or {}).get("user_id") if isinstance(state.loaded_context, dict) else None
    for value in (loaded_user_id, state.user_id, state.phone_number):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
