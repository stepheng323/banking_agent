from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import payout_reconciliation_consumer as reconciliation_module
from banking.transactions.runtime.consumers.payout_reconciliation_consumer import PayoutReconciliationConsumer
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


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


class _FakeProvider:
    provider_name = "flutterwave"

    def __init__(self, *, status_result: dict | None = None, reference_result: dict | None = None) -> None:
        self.status_result = status_result
        self.reference_result = reference_result
        self.status_calls: list[str] = []
        self.reference_calls: list[str] = []

    async def get_transfer_status(self, transaction_id: str) -> dict:
        self.status_calls.append(transaction_id)
        return self.status_result or {"success": False, "status": "pending", "transaction_id": transaction_id}

    async def get_transfer_by_reference(self, reference: str) -> dict:
        self.reference_calls.append(reference)
        return self.reference_result or {"success": False, "status": "pending", "reference": reference}


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str, str | None]] = []
        self.stale_calls: list[dict] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if transfer_id == str(self.transfer.id) else None

    async def get_by_payout_reference(self, payout_reference: str) -> SimpleNamespace | None:
        return self.transfer if payout_reference == self.transfer.payout_reference else None

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return self.transfer if idempotency_key == self.transfer.idempotency_key else None

    async def get_stale_pending_payout(self, *, cutoff, limit: int = 50) -> list[SimpleNamespace]:
        self.stale_calls.append({"cutoff": cutoff, "limit": limit})
        if self.transfer.status == FundedTransferStatusEnum.PAYOUT_PENDING.value:
            return [self.transfer]
        return []

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        self.status_updates.append((transfer_id, status, error_message))
        self.transfer.status = status
        if error_message:
            self.transfer.error_message = error_message


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace | None) -> None:
        self.tx = tx

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        if self.tx and self.tx.idempotency_key == idempotency_key:
            return self.tx
        return None


class _FakeFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps
        self.status_updates: list[tuple[str, str]] = []

    async def get_confirmed_for_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return [step for step in self.steps if step.status == FundingStepStatusEnum.CONFIRMED.value]

    async def update_status(self, step_id: str, status: str) -> None:
        self.status_updates.append((step_id, status))
        for step in self.steps:
            if str(step.id) == step_id:
                step.status = status


class _FakeLedgerAccounts:
    async def get_or_create(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(id=kwargs["code"], **kwargs)


class _FakeLedgerEntries:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def get_by_key(self, entry_key: str) -> SimpleNamespace | None:
        for entry in self.entries:
            if entry["entry_key"] == entry_key:
                return SimpleNamespace(**entry)
        return None

    async def create_entry_with_lines(self, **kwargs) -> SimpleNamespace:
        entry = {"id": kwargs["entry_key"], **kwargs}
        self.entries.append(entry)
        return SimpleNamespace(**entry)


class _SharedState:
    def __init__(
        self,
        transfer: SimpleNamespace,
        tx: SimpleNamespace | None = None,
        steps: list[SimpleNamespace] | None = None,
    ) -> None:
        self.db = _FakeDb()
        self.funded_transfers = _FakeFundedTransfers(transfer)
        self.transactions = _FakeTransactions(tx)
        self.funding_steps = _FakeFundingSteps(steps or [])
        self.ledger_accounts = _FakeLedgerAccounts()
        self.ledger_entries = _FakeLedgerEntries()
        self.commit_calls = 0


class _FakeUnitOfWork:
    def __init__(self, state: _SharedState) -> None:
        self._state = state
        self.db = state.db
        self.funded_transfers = state.funded_transfers
        self.transactions = state.transactions
        self.funding_steps = state.funding_steps
        self.ledger_accounts = state.ledger_accounts
        self.ledger_entries = state.ledger_entries

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self._state.commit_calls += 1


def _transfer() -> SimpleNamespace:
    return SimpleNamespace(
        id="funded-1",
        user_id="user-1",
        idempotency_key="idem-1",
        amount=5000,
        currency="NGN",
        payout_reference="trf-1",
        payout_retry_count=0,
        max_payout_retries=3,
        payout_provider=None,
        completed_at=None,
        error_message=None,
        status=FundedTransferStatusEnum.PAYOUT_PENDING.value,
    )


def _transaction() -> SimpleNamespace:
    return SimpleNamespace(
        id="tx-1",
        idempotency_key="idem-1",
        status=TransactionStatusEnum.PROCESSING.value,
        transaction_id=None,
        provider_status=None,
        provider_response=None,
        completed_at=None,
        error_message=None,
    )


def _step() -> SimpleNamespace:
    return SimpleNamespace(
        id="step-1",
        amount=5000,
        account_id="account-1",
        provider_reference="mono-ref-1",
        status=FundingStepStatusEnum.CONFIRMED.value,
    )


@pytest.mark.asyncio
async def test_reconciliation_completes_only_verified_success(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    state = _SharedState(transfer, tx)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": True,
            "status": "successful",
            "provider_status": "SUCCESSFUL",
            "transaction_id": "trf-1",
            "reference": "idem-1",
            "amount": 5000,
            "currency": "NGN",
        }
    )
    notifier = _Notifier()
    consumer = PayoutReconciliationConsumer(
        payout_provider=provider,
        publisher=_CapturePublisher(),
        notifier=notifier,
    )

    await consumer.process_job({"funded_transfer_id": "funded-1", "provider_transfer_id": "trf-1"})

    assert provider.status_calls == ["trf-1"]
    assert state.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.COMPLETED.value, None)]
    assert transfer.completed_at is not None
    assert tx.status == TransactionStatusEnum.SUCCESSFUL.value
    assert tx.transaction_id == "trf-1"
    assert state.commit_calls == 1
    assert notifier.calls == [(tx, "successful", None)]


@pytest.mark.asyncio
async def test_reconciliation_keeps_pending_and_increments_retry(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    state = _SharedState(transfer, tx)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": False,
            "status": "pending",
            "provider_status": "PENDING",
            "transaction_id": "trf-1",
            "reference": "idem-1",
            "amount": 5000,
            "currency": "NGN",
        }
    )
    publisher = _CapturePublisher()
    notifier = _Notifier()
    consumer = PayoutReconciliationConsumer(payout_provider=provider, publisher=publisher, notifier=notifier)

    await consumer.process_job({"funded_transfer_id": "funded-1", "provider_transfer_id": "trf-1"})

    assert state.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None)]
    assert transfer.payout_retry_count == 1
    assert tx.status == TransactionStatusEnum.PROCESSING.value
    assert publisher.published == []
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_reconciliation_failed_payout_queues_refunds(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    step = _step()
    state = _SharedState(transfer, tx, steps=[step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": False,
            "status": "failed",
            "provider_status": "FAILED",
            "transaction_id": "trf-1",
            "reference": "idem-1",
            "amount": 5000,
            "currency": "NGN",
            "error": "Transfer failed",
        }
    )
    publisher = _CapturePublisher()
    notifier = _Notifier()
    consumer = PayoutReconciliationConsumer(payout_provider=provider, publisher=publisher, notifier=notifier)

    await consumer.process_job({"funded_transfer_id": "funded-1", "provider_transfer_id": "trf-1"})

    assert state.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.REFUNDING.value, "Transfer failed")
    ]
    assert tx.status == TransactionStatusEnum.FAILED.value
    assert publisher.published == [
        (
            "refund.process",
            {
                "funding_step_id": "step-1",
                "funded_transfer_id": "funded-1",
                "amount": "5000.00",
                "amount_naira": "5000.00",
                "account_id": "account-1",
                "original_reference": "mono-ref-1",
            },
        )
    ]
    assert state.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUND_PENDING.value)]
    assert notifier.calls == [(tx, "failed", "Transfer failed")]


@pytest.mark.asyncio
async def test_reconciliation_mismatch_does_not_complete_or_refund(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    step = _step()
    state = _SharedState(transfer, tx, steps=[step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": True,
            "status": "successful",
            "provider_status": "SUCCESSFUL",
            "transaction_id": "trf-1",
            "reference": "different-reference",
            "amount": 5000,
            "currency": "NGN",
        }
    )
    publisher = _CapturePublisher()
    consumer = PayoutReconciliationConsumer(payout_provider=provider, publisher=publisher)

    await consumer.process_job({"funded_transfer_id": "funded-1", "provider_transfer_id": "trf-1"})

    assert transfer.status == FundedTransferStatusEnum.PAYOUT_PENDING.value
    assert transfer.error_message == "Payout reconciliation mismatch; manual review required"
    assert transfer.payout_retry_count == 1
    assert tx.status == TransactionStatusEnum.PROCESSING.value
    assert tx.provider_status == "reconciliation_mismatch"
    assert publisher.published == []
    assert state.funding_steps.status_updates == []


@pytest.mark.asyncio
async def test_reconciliation_falls_back_to_reference_lookup_when_transfer_id_is_not_found(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    state = _SharedState(transfer, tx)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": False,
            "status": "failed",
            "status_code": 404,
            "transaction_id": "trf-unknown",
            "error": "Transfer not found",
        },
        reference_result={
            "success": True,
            "status": "successful",
            "provider_status": "SUCCESSFUL",
            "transaction_id": "trf-1",
            "reference": "idem-1",
            "amount": 5000,
            "currency": "NGN",
        },
    )
    consumer = PayoutReconciliationConsumer(payout_provider=provider, publisher=_CapturePublisher())

    await consumer.process_job({"funded_transfer_id": "funded-1", "provider_transfer_id": "trf-unknown"})

    assert provider.status_calls == ["trf-unknown"]
    assert provider.reference_calls == ["idem-1"]
    assert transfer.status == FundedTransferStatusEnum.COMPLETED.value
    assert tx.status == TransactionStatusEnum.SUCCESSFUL.value


@pytest.mark.asyncio
async def test_reconciliation_batch_fetches_stale_pending_transfers(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    state = _SharedState(transfer, tx)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(
        status_result={
            "success": False,
            "status": "pending",
            "provider_status": "PENDING",
            "transaction_id": "trf-1",
            "reference": "idem-1",
            "amount": 5000,
            "currency": "NGN",
        }
    )
    consumer = PayoutReconciliationConsumer(payout_provider=provider, publisher=_CapturePublisher())

    await consumer.process_job({"limit": 7, "min_age_seconds": 60})

    assert state.funded_transfers.stale_calls[0]["limit"] == 7
    assert provider.status_calls == ["trf-1"]
    assert transfer.payout_retry_count == 1
