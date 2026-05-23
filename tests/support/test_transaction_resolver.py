from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.chat.src.agent.graphs.support.models import TransactionReference
from apps.chat.src.agent.graphs.support.resolver import TransactionResolver
from shared.config.settings import settings


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
        processing: list[object] | None = None,
        failed: list[object] | None = None,
    ) -> None:
        self._by_id = by_id
        self._by_idempotency_key = by_idempotency_key
        self._by_user = by_user or []
        self._pending = pending or []
        self._processing = processing or []
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
        if status == "processing":
            return list(self._processing)
        if status == "failed":
            return list(self._failed)
        return []


class _BankTransactionRepoStub:
    def __init__(self, rows: list[object] | None = None) -> None:
        self._rows = rows or []
        self.calls = 0

    async def list_by_user_window(self, user_id: str, *, start_date, end_date, provider: str = "mono", limit: int = 200):
        del user_id, start_date, end_date, provider, limit
        self.calls += 1
        return list(self._rows)


@pytest.fixture(autouse=True)
def _disable_unified_transaction_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_unified_transaction_view", False)


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


@pytest.mark.asyncio
async def test_resolver_recent_path_honors_status_priority() -> None:
    processing_tx = SimpleNamespace(
        id=uuid4(),
        status="processing",
        amount=6000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    failed_tx = SimpleNamespace(
        id=uuid4(),
        status="failed",
        amount=9000.0,
        recipient_name="Ada",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(
        by_user=[processing_tx, failed_tx],
        processing=[processing_tx],
        failed=[failed_tx],
    )
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=None,
        quoted_message_id=None,
        recent_status_priority=["failed", "processing", "pending"],
    )

    assert resolved == failed_tx
    assert method == "recent"
    assert tx_repo.get_by_status_calls == ["failed"]


@pytest.mark.asyncio
async def test_resolver_status_priority_uses_normalized_recent_status_variants() -> None:
    processing_tx = SimpleNamespace(
        id=uuid4(),
        status="processing",
        amount=6000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    error_tx = SimpleNamespace(
        id=uuid4(),
        status="error",
        amount=9000.0,
        recipient_name="Ada",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(
        by_user=[processing_tx, error_tx],
        processing=[processing_tx],
    )
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=None,
        quoted_message_id=None,
        recent_status_priority=["failed", "processing", "pending"],
    )

    assert resolved == error_tx
    assert method == "recent"
    assert tx_repo.get_by_status_calls == ["failed"]


@pytest.mark.asyncio
async def test_resolver_can_prefer_latest_recent_over_status_priority() -> None:
    processing_tx = SimpleNamespace(
        id=uuid4(),
        status="processing",
        amount=6000.0,
        recipient_name="Tolu",
        created_at=_utc_now_naive(),
    )
    failed_tx = SimpleNamespace(
        id=uuid4(),
        status="failed",
        amount=9000.0,
        recipient_name="Ada",
        created_at=_utc_now_naive(),
    )
    tx_repo = _TransactionRepoStub(
        by_user=[processing_tx, failed_tx],
        failed=[failed_tx],
    )
    resolver = TransactionResolver(
        transaction_repo=tx_repo,
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=None,
        quoted_message_id=None,
        recent_status_priority=["failed", "processing", "pending"],
        prefer_latest_recent=True,
    )

    assert resolved == processing_tx
    assert method == "recent"
    assert tx_repo.get_by_user_calls == 1
    assert tx_repo.get_by_status_calls == []


@pytest.mark.asyncio
async def test_resolver_unified_without_bank_repo_does_not_open_unit_of_work(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_unified_transaction_view", True)

    import shared.repositories.unit_of_work as unit_of_work_module

    class _ExplodingUnitOfWork:
        def __init__(self) -> None:
            raise AssertionError("support resolver should not open implicit bank-row unit of work")

    monkeypatch.setattr(unit_of_work_module, "UnitOfWork", _ExplodingUnitOfWork)

    local_failed = SimpleNamespace(
        id=uuid4(),
        transaction_type="transfer",
        status="failed",
        amount=6000.0,
        recipient_name="Tolu",
        recipient_account_number="1234567890",
        recipient_bank_name="Kuda",
        recipient_bank_code="999999",
        source_bank_name="GTBank",
        source_account_number="0123456789",
        transaction_id="local-failed",
        idempotency_key="idem-failed",
        provider_response={},
        error_message="Provider timeout",
        created_at=datetime(2026, 5, 16, 9, 0, 0),
        updated_at=datetime(2026, 5, 16, 9, 0, 0),
        completed_at=None,
    )
    resolver = TransactionResolver(
        transaction_repo=_TransactionRepoStub(by_user=[local_failed], failed=[local_failed]),
        actionable_message_repo=_ActionableRepoStub(actionable=None),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=None,
        quoted_message_id=None,
        recent_status_priority=["failed", "processing", "pending"],
        prefer_latest_recent=True,
    )

    assert method == "recent"
    assert isinstance(resolved, dict)
    assert resolved["unified_source"] == "local"
    assert resolved["recipient_name"] == "Tolu"


@pytest.mark.asyncio
async def test_resolver_unified_latest_can_return_bank_posted_transaction(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_unified_transaction_view", True)
    local_failed = SimpleNamespace(
        id=uuid4(),
        transaction_type="transfer",
        status="failed",
        amount=6000.0,
        recipient_name="Tolu",
        recipient_account_number="1234567890",
        recipient_bank_name="Kuda",
        recipient_bank_code="999999",
        source_bank_name="GTBank",
        source_account_number="0123456789",
        transaction_id="local-failed",
        idempotency_key="idem-failed",
        provider_response={},
        error_message="Provider timeout",
        created_at=datetime(2026, 5, 16, 9, 0, 0),
        updated_at=datetime(2026, 5, 16, 9, 0, 0),
        completed_at=None,
    )
    bank_posted = SimpleNamespace(
        provider_transaction_id="bank-latest",
        amount=950000.0,
        currency="NGN",
        transaction_type="credit",
        narration="Salary from Acme Corp",
        counterparty="Acme Corp",
        bank_name="GTBank",
        posted_at=datetime(2026, 5, 16, 10, 0, 0),
        posted_date=datetime(2026, 5, 16).date(),
    )
    resolver = TransactionResolver(
        transaction_repo=_TransactionRepoStub(by_user=[local_failed], failed=[local_failed]),
        actionable_message_repo=_ActionableRepoStub(actionable=None),
        bank_transaction_repo=_BankTransactionRepoStub([bank_posted]),
    )

    resolved, method = await resolver.resolve(
        user_id="u1",
        tx_ref=None,
        quoted_message_id=None,
        recent_status_priority=["failed", "processing", "pending"],
        prefer_latest_recent=True,
    )

    assert method == "recent"
    assert isinstance(resolved, dict)
    assert resolved["unified_source"] == "bank"
    assert resolved["status"] == "posted"
    assert resolved["counterparty"] == "Acme Corp"
