"""Field-update operation handling for semantic pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates

from ..flow.pending_action_confirmation_flow import _confirmation_edit_clarification_updates
from ..payloads.pending_action_payload_fields import _pending_edit_has_fields
from ..payloads.pending_action_payload_overrides import _pending_edit_payload_overrides_from_decision


def _resolve_field_update_operation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    decision: Any,
) -> dict[str, Any] | None:
    if decision.operation == "show_options" and getattr(decision, "show_options", None) is None:
        decision.show_options = True
    overrides = _pending_edit_payload_overrides_from_decision(
        state=state,
        interrupt=interrupt,
        decision=decision,
    )
    if overrides:
        return _continue_flow_updates(
            state,
            interrupt,
            precomputed_payload_overrides=overrides,
        )
    if getattr(interrupt, "kind", None) != "confirmation":
        return None
    if _pending_edit_has_fields(decision):
        return _confirmation_edit_clarification_updates(state, interrupt)
    return None


__all__ = ["_resolve_field_update_operation"]
