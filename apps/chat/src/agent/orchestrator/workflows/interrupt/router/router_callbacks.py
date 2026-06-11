from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view

_BATCH_PIN_FLOW_TYPES = {"batch", "transaction_batch", "transfer_batch", "multi_transfer", "mixed_batch"}
_BATCH_AUTHORIZABLE_TASK_TYPES = {"transfer", "airtime", "data", "schedule"}


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


def _callback_idempotency_key(state: OrchestratorState) -> str | None:
    callback = interrupt_state_view(state).last_callback or {}
    raw_key = callback.get("idempotency_key")
    if not isinstance(raw_key, str):
        return None
    key = raw_key.strip()
    return key or None


def _callback_flow_matches_interrupt(
    state: OrchestratorState,
    current_task_types: set[str],
) -> tuple[bool, str | None]:
    callback_flow_type = _callback_flow_type(state)
    if not callback_flow_type:
        return True, None
    if not current_task_types:
        return False, callback_flow_type
    if callback_flow_type in _BATCH_PIN_FLOW_TYPES:
        return current_task_types.issubset(_BATCH_AUTHORIZABLE_TASK_TYPES), callback_flow_type
    return callback_flow_type in current_task_types, callback_flow_type


def _interrupt_authorized_key_set(interrupt: PendingInterrupt) -> set[str]:
    keys: set[str] = set()
    if interrupt.authorization_idempotency_key:
        keys.add(interrupt.authorization_idempotency_key)
    keys.update(key for key in interrupt.authorized_task_idempotency_keys if key)
    return keys


def _loaded_context_user_id(state: OrchestratorState) -> str | None:
    state_view = interrupt_state_view(state)
    loaded_context = state_view.loaded_context_or_empty
    raw_user_id = loaded_context.get("user_id")
    if isinstance(raw_user_id, str) and raw_user_id.strip():
        return raw_user_id.strip()

    profile = loaded_context.get("profile")
    if isinstance(profile, dict):
        raw_profile_id = profile.get("id")
        if isinstance(raw_profile_id, str) and raw_profile_id.strip():
            return raw_profile_id.strip()
    return None


def _callback_user_matches_state(state: OrchestratorState, callback_user_id: str | None) -> bool:
    if not callback_user_id:
        return True

    loaded_user_id = _loaded_context_user_id(state)
    if loaded_user_id:
        return loaded_user_id == callback_user_id

    state_view = interrupt_state_view(state)
    state_user_id = str(state_view.user_id or "").strip()
    phone_number = str(state_view.phone_number or "").strip()
    if state_user_id and state_user_id != phone_number:
        return state_user_id == callback_user_id

    # Graph state is commonly keyed by phone number. PIN resume has already
    # verified that the callback user owns this phone before invoking the graph.
    return True


def _callback_authorization_matches_interrupt(
    state: OrchestratorState,
    interrupt: PendingInterrupt,
) -> tuple[bool, str | None, list[str]]:
    callback_key = _callback_idempotency_key(state)
    interrupt_keys = sorted(_interrupt_authorized_key_set(interrupt))
    if not callback_key or not interrupt_keys:
        return False, callback_key, interrupt_keys
    auth_context = interrupt_state_view(state).authorization_context
    if auth_context is None or auth_context.idempotency_key != callback_key:
        return False, callback_key, interrupt_keys
    if not _callback_user_matches_state(state, auth_context.user_id):
        return False, callback_key, interrupt_keys
    if callback_key not in set(interrupt_keys):
        return False, callback_key, interrupt_keys
    return True, callback_key, interrupt_keys


__all__ = [
    "_callback_authorization_matches_interrupt",
    "_callback_flow_matches_interrupt",
    "_callback_flow_type",
    "_callback_idempotency_key",
    "_is_verified_pin_callback",
]
