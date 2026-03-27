from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.core.src.agent.graphs.support.models import TransactionReference
from apps.core.src.agent.graphs.support.resolver import TransactionResolver


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _ActionableRepoStub:
    def __init__(self, actionable: object | None) -> None:
        self._actionable = actionable
        self.calls: list[tuple[str, str]] = []

    async def get_by_channel_message_id_for_user(self, channel_message_id: str, user_id: str) -> object | None:
        self.calls.append((channel_message_id, user_id))
        return self._actionable


class _TransactionRepoStub:
    def __init__(
        self,
        *,
        by_id: object | None = None,
        by_idempotency_key: object | None = None,
        by_user: list[object] | None = None,
        pending: list[object] | None = None,
        failed: list[object] | None = None,
    ) -> None:
        self._by_id = by_id
        self._by_idempotency_key = by_idempotency_key
        self._by_user = by_user or []
        self._pending = pending or []
        self._failed = failed or []
        self.get_by_id_calls = 0
        self.get_by_idempotency_key_calls = 0
        self.get_by_user_calls = 0
        self.get_by_status_calls: list[str] = []

    async def get_by_id(self, transaction_id: str):
        del transaction_id
        self.get_by_id_calls += 1
        return self._by_id

    async def get_by_idempotency_key(self, idempotency_key: str):
        del idempotency_key
        self.get_by_idempotency_key_calls += 1
        return self._by_idempotency_key

    async def get_by_user(self, user_id: str, limit: int = 20):
        del user_id, limit
        self.get_by_user_calls += 1
        return list(self._by_user)

    async def get_by_status(self, user_id: str, status: str):
        del user_id
        self.get_by_status_calls.append(status)
        if status == "pending":
            return list(self._pending)
        if status == "failed":
            return list(self._failed)
        return []


@pytest.mark.asyncio
async def test_resolver_uses_quoted_actionable_message_transaction_id() -> None:
    tx = SimpleNamespace(
        id=uuid4(),
        amount=5000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    resolver = TransactionResolver(
        transaction_repo=_TransactionRepoStub(by_id=tx),
        actionable_message_repo=_ActionableRepoStub(
            actionable=SimpleNamespace(message_data={"transaction_id": str(tx.id)})
        ),
    )

    resolved, method = await resolver.resolve(user_id="u1", tx_ref=None, quoted_message_id="wamid.receipt.1")

    assert resolved == tx
    assert method == "quoted"


@pytest.mark.asyncio
async def test_resolver_falls_back_to_idempotency_key_when_transaction_id_is_not_uuid() -> None:
    tx = SimpleNamespace(
        id=uuid4(),
        amount=5000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(by_id=None, by_idempotency_key=tx)
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(
            actionable=SimpleNamespace(message_data={"transaction_id": "idem-42"})
        ),
    )

    resolved, method = await resolver.resolve(user_id="u1", tx_ref=None, quoted_message_id="wamid.receipt.2")

    assert resolved == tx
    assert method == "quoted"
    assert tx_repo.get_by_id_calls == 1
    assert tx_repo.get_by_idempotency_key_calls == 1


@pytest.mark.asyncio
async def test_resolver_uses_idempotency_key_from_actionable_payload() -> None:
    tx = SimpleNamespace(
        id=uuid4(),
        amount=5000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(by_id=None, by_idempotency_key=tx)
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(
            actionable=SimpleNamespace(message_data={"idempotency_key": "idem-77"})
        ),
    )

    resolved, method = await resolver.resolve(user_id="u1", tx_ref=None, quoted_message_id="wamid.confirm.77")

    assert resolved == tx
    assert method == "quoted"
    assert tx_repo.get_by_id_calls == 1
    assert tx_repo.get_by_idempotency_key_calls == 1


@pytest.mark.asyncio
async def test_resolver_explicit_path_uses_async_get_by_user() -> None:
    tx = SimpleNamespace(
        id=uuid4(),
        amount=7500.0,
        recipient_name="Mercy Johnson",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(by_user=[tx])
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=TransactionReference(amount=7500.0, recipient_name="Mercy"),
        quoted_message_id=None,
    )

    assert resolved == tx
    assert method == "explicit"
    assert tx_repo.get_by_user_calls == 1


@pytest.mark.asyncio
async def test_resolver_recent_path_uses_async_status_lookups() -> None:
    tx = SimpleNamespace(
        id=uuid4(),
        amount=9000.0,
        recipient_name="Ada",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(pending=[tx])
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(user_id="u1", tx_ref=None, quoted_message_id=None)

    assert resolved == tx
    assert method == "recent"
    assert tx_repo.get_by_status_calls == ["pending"]
