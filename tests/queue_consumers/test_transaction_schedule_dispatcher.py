import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.policy.loader import get_cached_policy, load_policy
from banking.scheduling.services.transaction_schedule_dispatcher import TransactionScheduleDispatcher

CAPABILITY_POLICY_PATH = "banking/policy/defaults/capability_policy.json"
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
    def __init__(self) -> None:
        self.created_kwargs: dict[str, object] | None = None

    async def create(self, **kwargs) -> SimpleNamespace:
        self.created_kwargs = kwargs
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


def _schedule_fixture(domain: str = "transfer") -> SimpleNamespace:
    payload_snapshot: dict[str, object]
    if domain == "airtime":
        payload_snapshot = {
            "amount": 2000,
            "recipient_phone": "08162511023",
            "network": "MTN",
            "source_account_id": "acc-1",
            "source_account_number": "1234567890",
            "source_bank_name": "Zenith",
            "language": "en",
        }
    elif domain == "data":
        payload_snapshot = {
            "amount": 1500,
            "target_phone": "08162511023",
            "network": "MTN",
            "plan_code": "mtn-1gb",
            "plan_name": "1GB Daily",
            "biller_code": "BIL104",
            "plan_size_gb": 1.0,
            "plan_validity_days": 1,
            "plan_tags": ["daily"],
            "source_account_id": "acc-1",
            "source_account_number": "1234567890",
            "source_bank_name": "Zenith",
            "language": "en",
        }
    else:
        payload_snapshot = {
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
        }
    return SimpleNamespace(
        id="sch-1",
        user_id="user-1",
        domain=domain,
        next_run_at_utc=datetime(2026, 3, 5, 10, 0),
        recurrence_type="one_time",
        local_time="09:00",
        day_of_month=None,
        timezone="Africa/Lagos",
        status="active",
        channel="whatsapp",
        channel_identity="2348000000000",
        payload_snapshot=payload_snapshot,
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
        "banking.scheduling.services.transaction_schedule_dispatcher.UnitOfWork",
        lambda: fake_uow,
    )

    publisher = SimpleNamespace(publish=AsyncMock())
    dispatcher = TransactionScheduleDispatcher(publisher=publisher, max_due_per_tick=10)
    stats = await dispatcher.dispatch_due()

    assert stats["processed"] == 1
    assert stats["skipped"] == 0
    assert fake_uow.committed is True
    assert schedule.status == "completed"
    publisher.publish.assert_awaited_once()
    assert publisher.publish.await_args.kwargs["topic"] == "transaction.execute"
    create_kwargs = fake_uow.transactions.created_kwargs or {}
    assert create_kwargs["recipient_account_number"] == "8162511023"
    assert create_kwargs["recipient_bank_code"] == "033"
    assert create_kwargs["recipient_bank_name"] == "OPay"
    assert create_kwargs["recipient_name"] == "Mum"
    assert create_kwargs["target_phone_number"] is None
    assert create_kwargs["mobile_network"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "job_type", "payload_key"),
    [
        ("airtime", "execute_airtime", "airtime_data"),
        ("data", "execute_data", "data_purchase"),
    ],
)
async def test_dispatcher_enqueues_due_airtime_and_data_schedules(
    monkeypatch: pytest.MonkeyPatch,
    domain: str,
    job_type: str,
    payload_key: str,
) -> None:
    schedule = _schedule_fixture(domain)
    fake_uow = _FakeUow(schedule)
    monkeypatch.setattr(
        "banking.scheduling.services.transaction_schedule_dispatcher.UnitOfWork",
        lambda: fake_uow,
    )

    publisher = SimpleNamespace(publish=AsyncMock())
    dispatcher = TransactionScheduleDispatcher(publisher=publisher, max_due_per_tick=10)
    stats = await dispatcher.dispatch_due()

    assert stats == {"processed": 1, "skipped": 0}
    message = publisher.publish.await_args.kwargs["message"]
    assert message["type"] == job_type
    assert payload_key in message
    assert message["scheduled_meta"]["schedule_id"] == "sch-1"
    assert message["scheduled_meta"]["schedule_run_id"] == "run-1"
    create_kwargs = fake_uow.transactions.created_kwargs or {}
    assert create_kwargs["recipient_account_number"] is None
    assert create_kwargs["recipient_bank_code"] is None
    assert create_kwargs["recipient_bank_name"] is None
    assert create_kwargs["recipient_name"] is None
    assert create_kwargs["target_phone_number"] == "08162511023"
    assert create_kwargs["mobile_network"] == "MTN"
    if domain == "data":
        assert create_kwargs["biller_code"] == "BIL104"
        assert create_kwargs["biller_item_code"] == "mtn-1gb"
        assert create_kwargs["biller_item_name"] == "1GB Daily"
        assert create_kwargs["service_metadata"] == {
            "size_gb": 1.0,
            "validity_days": 1,
            "tags": ["daily"],
        }
        assert message[payload_key]["biller_code"] == "BIL104"
    else:
        assert create_kwargs["biller_code"] is None
        assert create_kwargs["biller_item_code"] is None
        assert create_kwargs["biller_item_name"] is None
        assert create_kwargs["service_metadata"] is None


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
            "banking.scheduling.services.transaction_schedule_dispatcher.UnitOfWork",
            lambda: fake_uow,
        )

        publisher = SimpleNamespace(publish=AsyncMock())
        dispatcher = TransactionScheduleDispatcher(publisher=publisher, max_due_per_tick=10)
        stats = await dispatcher.dispatch_due()

        assert stats == {"processed": 0, "skipped": 1}
        assert fake_uow.committed is True
        assert schedule.status == "active"
        publisher.publish.assert_not_awaited()
    finally:
        _reset_policy_cache()
