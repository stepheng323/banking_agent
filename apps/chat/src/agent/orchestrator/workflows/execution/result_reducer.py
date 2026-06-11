"""Transaction result reduction helpers for execution handlers."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    complete_task,
    fail_task,
    set_task_confirmation,
    set_task_payload_value,
    set_task_stage,
    update_task_payload,
)
from banking.runtime.results import TransactionOutcome
from banking.transfers.funding.plan_validation import FUNDING_ADJUSTMENT_REVIEW_STATE

WorkerResultPatch = dict[str, Any]


def _apply_result_patch(task: TaskSpec, result: Any) -> None:
    patch = getattr(result, "patch", None)
    if patch:
        update_task_payload(task, cast(WorkerResultPatch, patch))


def _set_confirmation(task: TaskSpec, result: Any, *, gate_on: str) -> None:
    if gate_on == "summary" and not getattr(result, "confirmation_summary", None):
        return
    if gate_on == "snapshot" and not getattr(result, "confirmation_snapshot", None):
        return

    set_task_confirmation(
        task,
        summary=getattr(result, "confirmation_summary", None),
        snapshot=getattr(result, "confirmation_snapshot", None),
        update_message=getattr(result, "update_message", None),
    )


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
        complete_task(task, receipt=result.receipt)
        if task.type == "transfer" and isinstance(result.receipt, dict):
            receipt_status = str(result.receipt.get("status") or "").strip().lower()
            if receipt_status == "processing":
                set_task_payload_value(task, "final_status", "processing")

    elif result.outcome == TransactionOutcome.NEEDS_INPUT:
        details = getattr(result, "details", None)
        review_state = details.get("review_state") if isinstance(details, dict) else None
        if task.type == "transfer" and review_state == FUNDING_ADJUSTMENT_REVIEW_STATE:
            set_task_stage(task, TaskStage.AWAITING_FUNDING_ADJUSTMENT)
        else:
            set_task_stage(task, TaskStage.EXTRACTED)
        accumulator.add_missing_fields(task_id, result.required_fields)
        accumulator.add_details(task_id, result.details)
        accumulator.add_prompt(result.prompt, task_id)
        accumulator.add_feedback_message(result.update_message)

        if hint := result.patch.get("source_bank_name"):
            accumulator.add_source_bank_hint(hint)

    elif result.outcome == TransactionOutcome.NEEDS_CONFIRMATION:
        set_task_stage(task, TaskStage.AWAITING_CONFIRMATION)
        accumulator.add_confirmation_task(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.NEEDS_AUTH:
        set_task_stage(task, TaskStage.AWAITING_AUTH)
        accumulator.add_auth_task(task_id)
        _set_confirmation(task, result, gate_on=confirmation_gate)

    elif result.outcome == TransactionOutcome.FAILED:
        if default_error is None:
            fail_task(task, result.error)
        else:
            fail_task(task, result.error or default_error)


__all__ = ["WorkerResultPatch", "_apply_result_patch", "_handle_transaction_outcome"]
