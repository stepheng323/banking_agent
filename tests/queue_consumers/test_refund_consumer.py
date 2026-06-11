from decimal import Decimal
from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import refund_consumer as refund_consumer_module
from banking.transactions.runtime.consumers.refund_consumer import RefundConsumer
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum


class _FakeFundingSteps:
    def __init__(self, step: SimpleNamespace, all_steps: list[SimpleNamespace] | None = None) -> None:
        self.step = step
        self.all_steps = all_steps or [step]
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_id(self, step_id: str) -> SimpleNamespace | None:
        return self.step if step_id == str(self.step.id) else None

    async def update_status(
        self,
        step_id: str,
        status: str,
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        del provider_reference, provider_debit_id
        self.status_updates.append((step_id, status, error_message))
        for step in self.all_steps:
            if str(step.id) == step_id:
                step.status = status

    async def get_by_transfer(self, funded_transfer_id: str) -> list[SimpleNamespace]:
        del funded_transfer_id
        return self.all_steps


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str]] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if transfer_id == str(self.transfer.id) else None

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        del error_message
        self.status_updates.append((transfer_id, status))
        self.transfer.status = status


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace) -> None:
        self.tx = tx

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return self.tx if self.tx.idempotency_key == idempotency_key else None


class _FakeLedgerAccounts:
    def __init__(self) -> None:
        self.accounts: dict[str, SimpleNamespace] = {}

    async def get_or_create(self, **kwargs) -> SimpleNamespace:
        account = self.accounts.get(kwargs["code"])
        if account:
            return account
        account = SimpleNamespace(id=kwargs["code"], **kwargs)
        self.accounts[kwargs["code"]] = account
        return account


class _FakeLedgerEntries:
    def __init__(self) -> None:
        self.entries: dict[str, SimpleNamespace] = {}

    async def get_by_key(self, entry_key: str) -> SimpleNamespace | None:
        return self.entries.get(entry_key)

    async def create_entry_with_lines(self, **kwargs) -> SimpleNamespace:
        entry = SimpleNamespace(id=kwargs["entry_key"], **kwargs)
        self.entries[kwargs["entry_key"]] = entry
        return entry


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


class _FakeUnitOfWork:
    def __init__(self, step: SimpleNamespace, all_steps: list[SimpleNamespace] | None = None) -> None:
        self.db = _FakeDb()
        self.transfer = SimpleNamespace(
            id="funded-1",
            idempotency_key="idem-1",
            user_id="user-1",
            amount=Decimal("1000.00"),
            status=FundedTransferStatusEnum.REFUNDING.value,
        )
        self.tx = SimpleNamespace(
            id="tx-1",
            idempotency_key="idem-1",
            status=TransactionStatusEnum.FAILED.value,
            provider_status=None,
            completed_at=None,
            error_message=None,
        )
        self.funding_steps = _FakeFundingSteps(step, all_steps)
        self.funded_transfers = _FakeFundedTransfers(self.transfer)
        self.transactions = _FakeTransactions(self.tx)
        self.ledger_accounts = _FakeLedgerAccounts()
        self.ledger_entries = _FakeLedgerEntries()
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


class _RefundProvider:
    def __init__(self, result: DebitResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def reverse_debit(self, debit_reference: str, reason: str = "Refund") -> DebitResult:
        self.calls.append((debit_reference, reason))
        return self.result


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[SimpleNamespace, str, str | None]] = []

    async def notify(
        self,
        transaction: SimpleNamespace,
        event: str,
        *,
        error_message: str | None = None,
    ) -> None:
        self.calls.append((transaction, event, error_message))


@pytest.mark.asyncio
async def test_refund_consumer_uses_provider_reference_for_mono_refund(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference="pool-ref-1",
        provider_debit_id="debit-1",
        amount=Decimal("1000.00"),
        refund_provider_id=None,
        refund_initiated_at=None,
        confirmed_at="now",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(
        DebitResult(success=True, status=DebitStatus.REVERSED, debit_id="refund-1", reference="pool-ref-1")
    )
    notifier = _Notifier()
    consumer = RefundConsumer(direct_debit_provider=provider, notifier=notifier)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == [("pool-ref-1", "Funding refund")]
    assert uow.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.REFUND_PROCESSING.value, None),
        ("step-1", FundingStepStatusEnum.REFUNDED.value, None),
    ]
    assert uow.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.REFUNDED.value)]
    assert "funding_step:step-1:mono_refund_confirmed" in uow.ledger_entries.entries
    assert step.refund_provider_id == "refund-1"
    assert uow.tx.status == TransactionStatusEnum.REVERSED.value
    assert uow.commit_calls == 2
    assert notifier.calls == [(uow.tx, "refunded", None)]


@pytest.mark.asyncio
async def test_refund_consumer_falls_back_to_original_reference_payload(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference=None,
        provider_debit_id="debit-1",
        amount=Decimal("1000.00"),
        refund_provider_id=None,
        refund_initiated_at=None,
        confirmed_at="now",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED, reference="pool-ref-1"))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job(
        {
            "funding_step_id": "step-1",
            "funded_transfer_id": "funded-1",
            "original_reference": "pool-ref-1",
        }
    )

    assert provider.calls == [("pool-ref-1", "Funding refund")]


@pytest.mark.asyncio
async def test_refund_consumer_fails_step_when_provider_reference_missing(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference=None,
        provider_debit_id="debit-1",
        amount=Decimal("1000.00"),
        refund_provider_id=None,
        refund_initiated_at=None,
        confirmed_at="now",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == []
    assert uow.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.REFUND_FAILED.value, "Provider reference missing for refund")
    ]


@pytest.mark.asyncio
async def test_refund_consumer_does_not_reinitiate_existing_refund(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PROCESSING.value,
        provider_reference="pool-ref-1",
        provider_debit_id="debit-1",
        amount=Decimal("1000.00"),
        refund_provider_id="refund-1",
        refund_initiated_at="now",
        confirmed_at="now",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED, reference="pool-ref-1"))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == []
    assert uow.funding_steps.status_updates == []


@pytest.mark.asyncio
async def test_refund_consumer_does_not_refund_confirmed_step_when_transfer_is_not_refunding(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.CONFIRMED.value,
        provider_reference="pool-ref-1",
        provider_debit_id="debit-1",
        amount=Decimal("1000.00"),
        refund_provider_id=None,
        refund_initiated_at=None,
        confirmed_at="now",
    )
    uow = _FakeUnitOfWork(step)
    uow.transfer.status = FundedTransferStatusEnum.COMPLETED.value
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED, reference="pool-ref-1"))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == []
    assert uow.funding_steps.status_updates == []
    assert uow.commit_calls == 0
