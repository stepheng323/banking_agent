from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from shared.database.enums import ActionableMessageTypeEnum


class _UsersRepoStub:
    def __init__(self, user: object | None, phone_user: object | None = None) -> None:
        self._user = user
        self._phone_user = phone_user
        self.calls: list[tuple[str, str]] = []
        self.phone_calls: list[str] = []

    async def get_by_channel_identity(self, channel: str, channel_user_id: str) -> object | None:
        self.calls.append((channel, channel_user_id))
        return self._user

    async def get_by_phone(self, phone_number: str) -> object | None:
        self.phone_calls.append(phone_number)
        return self._phone_user


class _ActionableDbStub:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, model: object) -> None:
        self.added.append(model)


class _ActionableRepoStub:
    def __init__(self, existing: object | None = None) -> None:
        self._existing = existing
        self.lookup_calls: list[tuple[str, str]] = []
        self.db = _ActionableDbStub()

    async def get_by_channel_message_id_for_user(self, channel_message_id: str, user_id: str) -> object | None:
        self.lookup_calls.append((channel_message_id, user_id))
        return self._existing


class _UowStub:
    def __init__(
        self,
        *,
        user: object | None,
        phone_user: object | None = None,
        existing_actionable: object | None = None,
        commit_error: Exception | None = None,
    ) -> None:
        self.users = _UsersRepoStub(user, phone_user=phone_user)
        self.actionable_messages = _ActionableRepoStub(existing_actionable)
        self._commit_error = commit_error
        self.rollback_called = False
        self.commit_called = False

    async def __aenter__(self) -> "_UowStub":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        del exc_type, exc_val, exc_tb
        return False

    async def commit(self) -> None:
        self.commit_called = True
        if self._commit_error:
            raise self._commit_error

    async def rollback(self) -> None:
        self.rollback_called = True


@pytest.mark.asyncio
async def test_actionable_consumer_persists_transfer_receipt_with_expected_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid4())
    uow = _UowStub(user=user)
    consumer = ActionableMessageConsumer(redis_queue=SimpleNamespace())
    monkeypatch.setattr("apps.core.src.queue_consumers.actionable_consumer.UnitOfWork", lambda: uow)

    before = datetime.utcnow()
    await consumer.process_job(
        {
            "channel": "whatsapp",
            "message_id": "wamid.123",
            "phone_number": "2348000000001",
            "payload": {"transaction_id": "tx-123"},
        }
    )
    after = datetime.utcnow()

    assert uow.users.calls == [("whatsapp", "2348000000001")]
    assert uow.commit_called is True
    assert len(uow.actionable_messages.db.added) == 1

    persisted = uow.actionable_messages.db.added[0]
    assert getattr(persisted, "message_type") == ActionableMessageTypeEnum.TRANSFER_RECEIPT.value
    assert getattr(persisted, "channel_message_id") == "wamid.123"
    assert getattr(persisted, "message_data") == {"transaction_id": "tx-123"}

    expires_at = getattr(persisted, "expires_at")
    assert before + timedelta(days=7) <= expires_at <= after + timedelta(days=7, seconds=1)


@pytest.mark.asyncio
async def test_actionable_consumer_ignores_duplicate_channel_message_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid4())
    duplicate_error = IntegrityError(
        "INSERT INTO actionable_messages ...",
        {},
        Exception('duplicate key value violates unique constraint "ix_actionable_messages_channel_message_id"'),
    )
    uow = _UowStub(user=user, commit_error=duplicate_error)
    consumer = ActionableMessageConsumer(redis_queue=SimpleNamespace())
    monkeypatch.setattr("apps.core.src.queue_consumers.actionable_consumer.UnitOfWork", lambda: uow)

    await consumer.process_job(
        {
            "channel": "telegram",
            "message_id": "52",
            "phone_number": "123456",
            "payload": {"transaction_id": "tx-123"},
        }
    )

    assert uow.commit_called is True
    assert uow.rollback_called is True


@pytest.mark.asyncio
async def test_actionable_consumer_skips_when_required_fields_or_user_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = ActionableMessageConsumer(redis_queue=SimpleNamespace())

    # Missing required fields should short-circuit before UoW is instantiated.
    monkeypatch.setattr(
        "apps.core.src.queue_consumers.actionable_consumer.UnitOfWork",
        lambda: (_ for _ in ()).throw(AssertionError("UnitOfWork should not be called")),
    )
    await consumer.process_job({"channel": "whatsapp", "message_id": "wamid.1", "payload": {"transaction_id": "x"}})

    # Missing user should short-circuit before persistence.
    uow = _UowStub(user=None)
    monkeypatch.setattr("apps.core.src.queue_consumers.actionable_consumer.UnitOfWork", lambda: uow)
    await consumer.process_job(
        {
            "channel": "whatsapp",
            "message_id": "wamid.2",
            "phone_number": "2348000000002",
            "payload": {"transaction_id": "x"},
        }
    )

    assert not uow.actionable_messages.db.added
    assert uow.commit_called is False


@pytest.mark.asyncio
async def test_actionable_consumer_does_not_fallback_to_phone_lookup_when_identity_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=uuid4())
    uow = _UowStub(user=None, phone_user=user)
    consumer = ActionableMessageConsumer(redis_queue=SimpleNamespace())
    monkeypatch.setattr("apps.core.src.queue_consumers.actionable_consumer.UnitOfWork", lambda: uow)

    await consumer.process_job(
        {
            "channel": "whatsapp",
            "message_id": "wamid.222",
            "phone_number": "2348000000022",
            "payload": {"idempotency_key": "idem-222"},
        }
    )

    assert uow.users.calls == [("whatsapp", "2348000000022")]
    assert uow.users.phone_calls == []
    assert uow.commit_called is False
    assert len(uow.actionable_messages.db.added) == 0
