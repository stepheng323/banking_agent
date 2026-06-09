"""Deterministic edits for pending confirmation interrupts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from banking.transfers.extraction.parsers import parse_confirmation_narration_edit

_BLOCKED_NARRATION_EDIT_STAGES = {
    TaskStage.AWAITING_AUTH,
    TaskStage.EXECUTING,
    TaskStage.COMPLETED,
    TaskStage.FAILED,
    TaskStage.CANCELLED,
}


def _confirmation_narration_edit_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    interrupt = runtime.interrupt
    if interrupt.kind != "confirmation":
        return None

    task_ids = [str(task_id) for task_id in interrupt.task_ids]
    if len(task_ids) != 1:
        return None

    state_view = interrupt_state_view(state)
    task_id = task_ids[0]
    task = state_view.task(task_id)
    if task is None or task.type != "transfer" or task.stage in _BLOCKED_NARRATION_EDIT_STAGES:
        return None

    patch = parse_confirmation_narration_edit(runtime.text)
    if patch is None:
        return None

    patch = {**patch, "skip_extraction": True}
    logger.info("confirmation_narration_edit_detected", task_id=task_id, source="deterministic")
    return _continue_flow_updates(
        state,
        interrupt,
        precomputed_payload_overrides={task_id: patch},
    )


__all__ = ["_confirmation_narration_edit_updates"]
