"""Canonical progress-stage selection at execution boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.progress import enter_task_progress


class _Tracker:
    def __init__(self) -> None:
        self.stages: list[tuple[str, dict[str, object] | None]] = []

    async def set_stage(self, stage_key: str, *, stage_metadata: dict[str, object] | None = None) -> None:
        self.stages.append((stage_key, stage_metadata))


def _context(tracker: _Tracker) -> SimpleNamespace:
    return SimpleNamespace(dependencies=SimpleNamespace(progress_tracker=tracker))


def _task(task_type: str, action: str, stage: TaskStage) -> TaskSpec:
    return TaskSpec(id="progress-test", type=task_type, stage=stage, payload={"action": action})  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_type", "action", "expected_stage"),
    [
        ("account", "unlink", "account.unlinking_accounts"),
        ("beneficiary", "delete_beneficiary", "beneficiary.deleting"),
        ("schedule", "pause_scheduled_transaction", "schedule.pausing"),
        ("transfer", "schedule_transfer", "schedule.creating"),
        ("support", "close_support_ticket", "support.closing_ticket"),
        ("airtime", "buy_airtime", "airtime.processing_purchase"),
        ("data", "buy_data", "data.processing_purchase"),
    ],
)
async def test_executing_mutations_select_their_registered_progress_stage(
    task_type: str,
    action: str,
    expected_stage: str,
) -> None:
    tracker = _Tracker()

    await enter_task_progress(_context(tracker), _task(task_type, action, TaskStage.EXECUTING))  # type: ignore[arg-type]

    assert tracker.stages == [(expected_stage, None)]


@pytest.mark.asyncio
async def test_fast_reads_and_pre_confirmation_purchases_do_not_select_progress() -> None:
    tracker = _Tracker()
    ctx = _context(tracker)

    for task in (
        _task("account", "check_balance", TaskStage.DRAFT),
        _task("beneficiary", "list_beneficiaries", TaskStage.DRAFT),
        _task("schedule", "list_scheduled_transactions", TaskStage.DRAFT),
        _task("support", "list_support_tickets", TaskStage.DRAFT),
        _task("data", "data_plan_query", TaskStage.EXECUTING),
        _task("airtime", "buy_airtime", TaskStage.DRAFT),
        _task("data", "buy_data", TaskStage.AWAITING_AUTH),
    ):
        await enter_task_progress(ctx, task)  # type: ignore[arg-type]

    assert tracker.stages == []


@pytest.mark.asyncio
async def test_link_and_ticket_note_can_report_without_pin_execution() -> None:
    tracker = _Tracker()
    ctx = _context(tracker)

    await enter_task_progress(ctx, _task("account", "link", TaskStage.DRAFT))  # type: ignore[arg-type]
    await enter_task_progress(ctx, _task("support", "append_support_ticket_note", TaskStage.DRAFT))  # type: ignore[arg-type]

    assert tracker.stages == [
        ("account.linking_account", None),
        ("support.updating_ticket", None),
    ]
