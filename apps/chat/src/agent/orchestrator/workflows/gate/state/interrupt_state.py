"""Pending-interrupt state checks for the gate runner."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import gate_state_view


def _pending_interrupt_task_types(state: OrchestratorState) -> set[str]:
    return gate_state_view(state).pending_interrupt_task_types


def _has_live_pending_interrupt(state: OrchestratorState) -> bool:
    return gate_state_view(state).has_live_pending_interrupt


def _is_numeric_input_interrupt_selection(state: OrchestratorState, message_text: str) -> bool:
    return gate_state_view(state).is_numeric_input_interrupt_selection(message_text)
