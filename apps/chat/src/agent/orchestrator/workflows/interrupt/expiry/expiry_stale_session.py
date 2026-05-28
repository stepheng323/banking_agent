from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_repeat import (
    _resolve_deterministic_confirmation_repeat_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _active_intent
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_selection_route import (
    _resolve_deterministic_input_selection_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_slot_route import (
    _resolve_deterministic_input_slot_route,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_edit_engine import (
    PendingActionEditEngine,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_callbacks import _is_verified_pin_callback
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_core import (
    _resolve_deterministic_status_query_route,
    _route_interrupt,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.router.router_switch import (
    _is_same_flow_transactional_switch,
)


async def _expired_transaction_message_targets_stale_session(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    task_planner: Any,
    text: str,
) -> bool:
    """Return True when the post-expiry turn is trying to continue the expired flow."""
    if _is_verified_pin_callback(state) and getattr(interrupt, "kind", None) in {"confirmation", "auth"}:
        return True

    status_shortcut_route = _resolve_deterministic_status_query_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if status_shortcut_route is not None:
        return True

    if _resolve_deterministic_input_selection_route(state=state, interrupt=interrupt, text=text) is not None:
        return True

    if _resolve_deterministic_input_slot_route(state=state, interrupt=interrupt, text=text) is not None:
        return True

    if _resolve_deterministic_confirmation_repeat_route(state=state, interrupt=interrupt, text=text) is not None:
        return True

    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    shortcut_route, _miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=getattr(interrupt, "kind", ""),
        locale=shortcut_locale,
    )
    if shortcut_route is not None:
        return shortcut_route.decision in {"approve_flow", "reject_flow", "cancel", "status_query", "continue_flow"}

    route = await _route_interrupt(
        task_planner=task_planner,
        state=state,
        text=text,
        kind=getattr(interrupt, "kind", ""),
        task_ids=getattr(interrupt, "task_ids", []),
        current_task_types=current_task_types,
        fields_by_task=getattr(interrupt, "fields_by_task", {}),
        prompt=getattr(interrupt, "prompt", None),
    )
    if route.decision in {"approve_flow", "reject_flow", "cancel", "status_query", "continue_flow"}:
        return True
    if route.decision == "switch_intent":
        if str(route.target_mode or "").strip().lower() == "new":
            return False
        return _is_same_flow_transactional_switch(
            route=route,
            interrupt=interrupt,
            active_type=_active_intent(current_task_types),
        )

    resolution = await PendingActionEditEngine().interpret(
        state=state,
        interrupt=interrupt,
        text=text,
        task_planner=task_planner,
    )
    return resolution is not None


__all__ = ["_expired_transaction_message_targets_stale_session"]
