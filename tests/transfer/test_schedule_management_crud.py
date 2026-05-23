from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.transfer.models.types import TransferGates, TransferPayload
from apps.chat.src.agent.graphs.transfer.worker import TransferWorker
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from shared.services.scheduling.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)


class _FakeScheduleRepo:
    def __init__(self, schedules: list[SimpleNamespace]) -> None:
        self.schedules = schedules

    async def get_active_by_user(self, user_id: str, limit: int = 20) -> list[SimpleNamespace]:
        del user_id, limit
        return [schedule for schedule in self.schedules if schedule.status == "active"]

    async def get_active_for_user_for_update(self, schedule_id: str, user_id: str) -> SimpleNamespace | None:
        del user_id
        return next(
            (
                schedule
                for schedule in self.schedules
                if str(schedule.id) == schedule_id and schedule.status == "active"
            ),
            None,
        )


class _FakeUnitOfWork:
    def __init__(self, repo: _FakeScheduleRepo) -> None:
        self.scheduled_instructions = repo
        self.committed = False

    async def __aenter__(self) -> "_FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        del exc_type, exc_val, exc_tb
        return False

    async def commit(self) -> None:
        self.committed = True


def _worker() -> TransferWorker:
    return TransferWorker(
        validation_service=None,
        publisher=None,
        extractor=None,
        resolver_provider=None,
        bank_cache=None,
        transaction_repo=None,
    )


def _future_schedule_date() -> date:
    return today_lagos() + timedelta(days=1)


def _expected_next_run_at(local_time: str) -> datetime:
    next_run_at = compute_initial_next_run_utc(
        recurrence_type="one_time",
        start_date=_future_schedule_date().isoformat(),
        local_time=local_time,
        timezone=SCHEDULE_TIMEZONE,
    )
    assert next_run_at is not None
    return next_run_at


def _schedule(
    schedule_id: str,
    *,
    domain: str,
    payload_snapshot: dict,
    local_time: str = "08:00",
) -> SimpleNamespace:
    next_run_at = _expected_next_run_at(local_time)
    return SimpleNamespace(
        id=schedule_id,
        user_id="user-1",
        domain=domain,
        status="active",
        action=f"schedule_{domain}",
        payload_snapshot=payload_snapshot,
        timezone="Africa/Lagos",
        recurrence_type="one_time",
        start_date=_future_schedule_date().isoformat(),
        local_time=local_time,
        day_of_week=None,
        day_of_month=None,
        end_date=None,
        next_run_at_utc=next_run_at,
        cancelled_at=None,
    )


@pytest.mark.asyncio
async def test_schedule_management_list_shows_transfer_airtime_and_data(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo(
        [
            _schedule(
                "sch-transfer",
                domain="transfer",
                payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
            ),
            _schedule(
                "sch-airtime",
                domain="airtime",
                payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
            ),
            _schedule(
                "sch-data",
                domain="data",
                payload_snapshot={"amount": 1500, "target_phone": "08162511023", "plan_name": "1GB Daily"},
            ),
        ]
    )
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _worker()._list_schedules(user_id="user-1", locale="en")

    assert result.outcome == TransactionOutcome.OK
    assert result.response
    assert "Scheduled transactions:" in result.response
    assert "Transfer:" in result.response
    assert "Airtime:" in result.response
    assert "Data:" in result.response
    assert "sch-transfer" not in result.response
    assert "ID:" not in result.response


@pytest.mark.asyncio
async def test_schedule_management_count_mode_reports_pending_count(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo(
        [
            _schedule(
                "sch-transfer",
                domain="transfer",
                payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
            ),
            _schedule(
                "sch-airtime",
                domain="airtime",
                payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
            ),
        ]
    )
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _worker()._list_schedules(
        data=TransferPayload(schedule_response_mode="count"),
        user_id="user-1",
        locale="en",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You have 2 pending scheduled transactions."
    assert result.patch["schedule_context_items"][0]["label"].startswith("Transfer:")
    assert result.patch["schedule_context_items"][1]["data"]["domain_key"] == "airtime"


@pytest.mark.asyncio
async def test_schedule_management_cancel_deletes_by_disabling_active_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-airtime",
        domain="airtime",
        payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: uow)

    result = await _worker()._cancel_schedule(
        data=TransferPayload(schedule_selector="1"),
        user_id="user-1",
        locale="en",
        user_message="delete scheduled airtime",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "Scheduled transaction cancelled."
    assert schedule.status == "cancelled"
    assert schedule.cancelled_at is not None
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_material_edit_requires_pin_without_text_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: uow)
    worker = _worker()
    payload = TransferPayload(
        schedule_selector="1",
        amount=7000,
        schedule_time_local="09:30",
    )

    auth = await worker._edit_schedule(
        data=payload,
        user_id="user-1",
        locale="en",
        user_message="change scheduled transfer to 7k at 9:30am",
        gates=TransferGates(),
    )

    assert auth.outcome == TransactionOutcome.NEEDS_AUTH
    assert auth.confirmation_summary
    assert "Confirm Schedule Update" not in auth.confirmation_summary
    assert "One Time at 9:30 AM WAT" in auth.confirmation_summary
    assert auth.patch["schedule_edit_requires_auth"] is True
    assert schedule.payload_snapshot["amount"] == 5000

    updated = await worker._edit_schedule(
        data=payload.model_copy(update=auth.patch),
        user_id="user-1",
        locale="en",
        user_message="123456",
        gates=TransferGates(pin_verified=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert updated.response is not None
    expected_next_run_at = _expected_next_run_at("09:30")
    assert "amount changed from ₦5,000 to ₦7,000" in updated.response
    assert f"Next run: {format_lagos_schedule_datetime(expected_next_run_at)}" in updated.response
    assert schedule.payload_snapshot["amount"] == 7000
    assert schedule.local_time == "09:30"
    assert schedule.next_run_at_utc == expected_next_run_at
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_time_only_edit_updates_after_confirmation_without_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: uow)
    worker = _worker()
    payload = TransferPayload(
        schedule_selector="1",
        schedule_time_local="09:30",
    )

    confirmation = await worker._edit_schedule(
        data=payload,
        user_id="user-1",
        locale="en",
        user_message="change scheduled transfer to 9:30am",
        gates=TransferGates(),
    )

    assert confirmation.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert confirmation.patch["schedule_edit_requires_auth"] is False
    assert schedule.local_time == "08:00"

    updated = await worker._edit_schedule(
        data=payload.model_copy(update=confirmation.patch),
        user_id="user-1",
        locale="en",
        user_message="yes",
        gates=TransferGates(confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert updated.response is not None
    expected_next_run_at = _expected_next_run_at("09:30")
    assert "time changed from 8:00 AM to 9:30 AM WAT" in updated.response
    assert f"Next run: {format_lagos_schedule_datetime(expected_next_run_at)}" in updated.response
    assert schedule.payload_snapshot["amount"] == 5000
    assert schedule.local_time == "09:30"
    assert schedule.next_run_at_utc == expected_next_run_at
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_narration_only_edit_does_not_require_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum", "narration": "Old note"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: uow)
    worker = _worker()

    confirmation = await worker._edit_schedule(
        data=TransferPayload(schedule_selector="1", narration="New note"),
        user_id="user-1",
        locale="en",
        user_message="change the narration to new note",
        gates=TransferGates(),
    )

    assert confirmation.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert confirmation.patch["schedule_edit_requires_auth"] is False

    updated = await worker._edit_schedule(
        data=TransferPayload(schedule_selector="1").model_copy(update=confirmation.patch),
        user_id="user-1",
        locale="en",
        user_message="yes",
        gates=TransferGates(confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert schedule.payload_snapshot["narration"] == "New note"


@pytest.mark.asyncio
async def test_schedule_management_recurrence_edit_requires_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("apps.chat.src.agent.graphs.transfer.worker.UnitOfWork", lambda: uow)
    worker = _worker()

    auth = await worker._edit_schedule(
        data=TransferPayload(schedule_selector="1", recurrence_type="daily", schedule_time_local="09:30"),
        user_id="user-1",
        locale="en",
        user_message="make it daily at 9:30am",
        gates=TransferGates(),
    )

    assert auth.outcome == TransactionOutcome.NEEDS_AUTH
    assert auth.patch["schedule_edit_requires_auth"] is True
    assert schedule.recurrence_type == "one_time"
