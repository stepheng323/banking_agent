from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.core.src.schedulers.transfer_schedule_dispatcher import TransferScheduleDispatcher


class _FakeScheduledInstructionRepo:
    def __init__(self, schedule: SimpleNamespace) -> None:
        self._schedule = schedule

    async def get_due_active(self, *, now_utc: datetime, limit: int) -> list[SimpleNamespace]:
        del now_utc, limit
        return [self._schedule]


class _FakeScheduledRunRepo:
    async def get_by_idempotency_key(self, idempotency_key: str) -> None:
        del idempotency_key
        return None

    async def create(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(id="run-1", **kwargs)


class _FakeTransactionRepo:
    async def create(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(id="tx-1", **kwargs)


class _FakeUow:
    def __init__(self, schedule: SimpleNamespace) -> None:
        self.scheduled_instructions = _FakeScheduledInstructionRepo(schedule)
        self.scheduled_runs = _FakeScheduledRunRepo()
        self.transactions = _FakeTransactionRepo()
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        del exc_type, exc_val, exc_tb
        return False

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_dispatcher_enqueues_due_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(
        id="sch-1",
        user_id="user-1",
        next_run_at_utc=datetime(2026, 3, 5, 10, 0),
        recurrence_type="one_time",
        local_time="09:00",
        day_of_month=None,
        timezone="Africa/Lagos",
        status="active",
        channel="whatsapp",
        channel_identity="2348000000000",
        payload_snapshot={
            "amount": 10000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
            "recipient_bank_name": "OPay",
            "recipient_name": "Mum",
            "source_account_id": "acc-1",
            "source_account_number": "1234567890",
            "source_bank_name": "Zenith",
            "narration": "Family support",
            "language": "en",
            "source_account_name": "Main",
        },
    )
    fake_uow = _FakeUow(schedule)
    monkeypatch.setattr(
        "apps.core.src.schedulers.transfer_schedule_dispatcher.UnitOfWork",
        lambda: fake_uow,
    )

    publisher = SimpleNamespace(publish=AsyncMock())
    dispatcher = TransferScheduleDispatcher(publisher=publisher, max_due_per_tick=10)
    stats = await dispatcher.dispatch_due()

    assert stats["processed"] == 1
    assert stats["skipped"] == 0
    assert fake_uow.committed is True
    assert schedule.status == "completed"
    publisher.publish.assert_awaited_once()
    assert publisher.publish.await_args.kwargs["topic"] == "transaction.execute"
