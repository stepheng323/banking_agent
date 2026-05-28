"""Pending-interrupt state checks for the gate runner."""

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _pending_interrupt_task_types(state: OrchestratorState) -> set[str]:
    interrupt = state.pending_interrupt
    if interrupt is None or not isinstance(getattr(interrupt, "task_ids", None), list):
        return set()
    return {
        state.tasks[task_id].type
        for task_id in interrupt.task_ids
        if isinstance(task_id, str) and task_id in state.tasks
    }


def _has_live_pending_interrupt(state: OrchestratorState) -> bool:
    interrupt = state.pending_interrupt
    if interrupt is None:
        return False

    task_types = _pending_interrupt_task_types(state)
    if not task_types:
        return False

    interrupt_kind = getattr(interrupt, "kind", None)
    if interrupt_kind in {"confirmation", "auth"}:
        return True
    if interrupt_kind != "input":
        return True

    # Active query sessions own their own follow-up semantics and should not pay interrupt-router cost.
    return any(task_type != "query" for task_type in task_types)


def _is_numeric_input_interrupt_selection(state: OrchestratorState, message_text: str) -> bool:
    interrupt = state.pending_interrupt
    if interrupt is None or getattr(interrupt, "kind", None) != "input":
        return False
    if not message_text.strip().isdigit():
        return False

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return False

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = fields_by_task.get(task_ids[0]) or []
    return set(required_fields) == {"source_account_id"}
