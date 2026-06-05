"""Active-flow summary extraction for turn context."""

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary_payload import (
    _compact_payload_for_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView


@dataclass(frozen=True)
class ActiveFlowDetails:
    summary: str | None
    intent: str | None
    missing_fields: list[str]
    interrupt_kind: str | None


def _build_active_flow_details(state_view: PlannerStateView) -> ActiveFlowDetails:
    interrupt_kind = state_view.pending_interrupt_kind
    active_task = state_view.current_wave_first_task
    if not active_task:
        return ActiveFlowDetails(None, None, [], interrupt_kind)

    payload_view = {
        key: value for key, value in active_task.payload.items() if key not in ["result", "error", "confirmation"]
    }
    payload_preview = _compact_payload_for_prompt(payload_view)
    summary = (
        f"Active Flow: {active_task.type.upper()} (User is currently in this flow).\n"
        f"Current Task Data: {payload_preview}\n"
        f"Routing: slot_updates_keep_intent_unless_user_clearly_switches"
    )
    missing_fields = state_view.pending_interrupt_fields_for_task(active_task.id)
    return ActiveFlowDetails(summary, active_task.type, missing_fields, interrupt_kind)


__all__ = ["ActiveFlowDetails", "_build_active_flow_details"]
