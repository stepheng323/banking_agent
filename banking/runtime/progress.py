"""Canonical, user-visible progress stages for execution operations."""

from __future__ import annotations

from dataclasses import dataclass

from banking.runtime.operations import operation_spec


@dataclass(frozen=True, slots=True)
class WorkerProgressStageSpec:
    """Progress policy for one canonical worker operation."""

    executor: str
    action: str
    stage_key: str
    requires_executing_stage: bool = True


def _stage(
    executor: str,
    action: str,
    stage_key: str,
    *,
    requires_executing_stage: bool = True,
) -> WorkerProgressStageSpec:
    # Keep the progress registry coupled to executable canonical operations.
    operation_spec(executor, action)
    return WorkerProgressStageSpec(
        executor=executor,
        action=action,
        stage_key=stage_key,
        requires_executing_stage=requires_executing_stage,
    )


_PROGRESS_STAGES = (
    _stage("transfer", "schedule_transfer", "schedule.creating"),
    _stage("transfer", "recurring_transfer", "schedule.creating"),
    _stage("airtime", "schedule_airtime", "schedule.creating"),
    _stage("airtime", "recurring_airtime", "schedule.creating"),
    _stage("data", "schedule_data", "schedule.creating"),
    _stage("data", "recurring_data", "schedule.creating"),
    _stage("account", "link", "account.linking_account", requires_executing_stage=False),
    _stage("account", "reinitiate_mandate", "account.reinitiating_mandate"),
    _stage("account", "set_default", "account.setting_default"),
    _stage("account", "unlink", "account.unlinking_accounts"),
    _stage("beneficiary", "rename_beneficiary", "beneficiary.renaming"),
    _stage("beneficiary", "delete_beneficiary", "beneficiary.deleting"),
    _stage("schedule", "edit_scheduled_transaction", "schedule.updating"),
    _stage("schedule", "cancel_scheduled_transaction", "schedule.cancelling"),
    _stage("schedule", "pause_scheduled_transaction", "schedule.pausing"),
    _stage("schedule", "resume_scheduled_transaction", "schedule.resuming"),
    _stage("support", "append_support_ticket_note", "support.updating_ticket", requires_executing_stage=False),
    _stage("support", "close_support_ticket", "support.closing_ticket"),
    _stage("airtime", "buy_airtime", "airtime.processing_purchase"),
    _stage("data", "buy_data", "data.processing_purchase"),
)

WORKER_PROGRESS_STAGES: dict[tuple[str, str], WorkerProgressStageSpec] = {
    (spec.executor, spec.action): spec for spec in _PROGRESS_STAGES
}


def operation_progress_spec(executor: str, action: str) -> WorkerProgressStageSpec | None:
    """Return progress policy for a canonical operation, if it has one."""

    return WORKER_PROGRESS_STAGES.get((str(executor).strip().lower(), str(action).strip().lower()))


__all__ = ["WORKER_PROGRESS_STAGES", "WorkerProgressStageSpec", "operation_progress_spec"]
