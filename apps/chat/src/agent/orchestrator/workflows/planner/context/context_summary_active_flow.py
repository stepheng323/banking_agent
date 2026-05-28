"""Active-flow summary extraction for turn context."""

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary_payload import (
    _compact_payload_for_prompt,
)


@dataclass(frozen=True)
class ActiveFlowDetails:
    summary: str | None
    intent: str | None
    missing_fields: list[str]
    interrupt_kind: str | None


def _build_active_flow_details(state: OrchestratorState) -> ActiveFlowDetails:
    interrupt_kind = state.pending_interrupt.kind if state.pending_interrupt else None
    if not state.waves or state.current_wave_index >= len(state.waves):
        return ActiveFlowDetails(None, None, [], interrupt_kind)

    current_wave = state.waves[state.current_wave_index]
    if not current_wave:
        return ActiveFlowDetails(None, None, [], interrupt_kind)

    active_task = state.tasks.get(current_wave[0])
    if not active_task:
        return ActiveFlowDetails(None, None, [], interrupt_kind)

    payload_view = {
        key: value
        for key, value in active_task.payload.items()
        if key not in ["result", "error", "confirmation"]
    }
    payload_preview = _compact_payload_for_prompt(payload_view)
    summary = (
        f"Active Flow: {active_task.type.upper()} (User is currently in this flow).\n"
        f"Current Task Data: {payload_preview}\n"
        f"Routing: slot_updates_keep_intent_unless_user_clearly_switches"
    )
    missing_fields: list[str] = []
    if state.pending_interrupt and active_task.id in state.pending_interrupt.task_ids:
        missing_fields = list(state.pending_interrupt.fields_by_task.get(active_task.id, []) or [])
    return ActiveFlowDetails(summary, active_task.type, missing_fields, interrupt_kind)


__all__ = ["ActiveFlowDetails", "_build_active_flow_details"]
