"""Transaction result reduction helpers for execution handlers."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from banking.runtime.results import TransactionOutcome

WorkerResultPatch = dict[str, Any]


def _apply_result_patch(task: TaskSpec, result: Any) -> None:
    patch = getattr(result, "patch", None)
    if patch:
        task.payload.update(cast(WorkerResultPatch, patch))


def _set_confirmation(task: TaskSpec, result: Any, *, gate_on: str) -> None:
    if gate_on == "summary" and not getattr(result, "confirmation_summary", None):
        return
    if gate_on == "snapshot" and not getattr(result, "confirmation_snapshot", None):
        return

    confirmation = task.payload.setdefault("confirmation", {})
    confirmation["summary"] = getattr(result, "confirmation_summary", None)
    confirmation["snapshot"] = getattr(result, "confirmation_snapshot", None)
    update_message = getattr(result, "update_message", None)
    if update_message:
        confirmation["update_message"] = update_message
    else:
        confirmation.pop("update_message", None)
    task.payload.pop("transition_acknowledgment", None)
    task.payload.pop("previous_confirmation_snapshot", None)


def _handle_transaction_outcome(
    task: TaskSpec,
    task_id: str,
    result: Any,
    accumulator: ExecutionAccumulator,
    *,
    confirmation_gate: str,
    default_error: str | None,
) -> None:
    if result.outcome == TransactionOutcome.OK:
        task.stage = TaskStage.COMPLETED
        if result.receipt:
            task.payload["receipt"] = result.receipt

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        task.stage = TaskStage.EXTRACTED
        accumulator.add_missing_fields(task_id, result.required_fields)
        accumulator.add_details(task_id, result.details)
        accumulator.add_prompt(result.prompt, task_id)
        if result.update_message:
            accumulator.feedback_messages.append(result.update_message)

        if hint := result.patch.get("source_bank_name"):
            accumulator.source_bank_hints.append(hint)

    elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
        task.stage = TaskStage.AWAITING_CONFIRMATION
        accumulator.needs_confirm_tasks.append(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.NEEDS_AUTH:
        task.stage = TaskStage.AWAITING_AUTH
        accumulator.needs_auth_tasks.append(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.FAILED:
        task.stage = TaskStage.FAILED
        if default_error is None:
            task.payload["error"] = result.error
        else:
            task.payload["error"] = result.error or default_error


__all__ = ["WorkerResultPatch", "_apply_result_patch", "_handle_transaction_outcome"]
