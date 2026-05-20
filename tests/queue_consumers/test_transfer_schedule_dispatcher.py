import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.schedulers.transfer_schedule_dispatcher import TransferScheduleDispatcher
from shared.policy.loader import get_cached_policy, load_policy

CAPABILITY_POLICY_PATH = "config/capability_policy.json"
SCHEDULE_DISABLED_MESSAGE = (
    "Scheduled payments are temporarily unavailable. I can still help with immediate transfers, airtime/data purchase, "
    "balances, and transaction queries."
)


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


def _schedule_fixture() -> SimpleNamespace:
    return SimpleNamespace(
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


def _install_disabled_schedule_policy(tmp_path: Path) -> None:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"]["schedule"]["enabled"] = False
    raw["capability_matrix"]["schedule"]["limitation_message"] = SCHEDULE_DISABLED_MESSAGE
    policy_path = tmp_path / "capability_policy_schedule_disabled.json"
    policy_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
    get_cached_policy(path=str(policy_path), force_reload=True)


def _reset_policy_cache() -> None:
    get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


@pytest.mark.asyncio
async def test_dispatcher_enqueues_due_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule_fixture()
    fake_uow = _FakeUow(schedule)
    monkeypatch.setattr(
        "apps.chat.src.schedulers.transfer_schedule_dispatcher.UnitOfWork",
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


@pytest.mark.asyncio
async def test_dispatcher_skips_due_schedule_when_schedule_domain_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_disabled_schedule_policy(tmp_path)
    try:
        schedule = _schedule_fixture()
        fake_uow = _FakeUow(schedule)
        monkeypatch.setattr(
            "apps.chat.src.schedulers.transfer_schedule_dispatcher.UnitOfWork",
            lambda: fake_uow,
        )

        publisher = SimpleNamespace(publish=AsyncMock())
        dispatcher = TransferScheduleDispatcher(publisher=publisher, max_due_per_tick=10)
        stats = await dispatcher.dispatch_due()

        assert stats == {"processed": 0, "skipped": 1}
        assert fake_uow.committed is True
        assert schedule.status == "active"
        publisher.publish.assert_not_awaited()
    finally:
        _reset_policy_cache()
