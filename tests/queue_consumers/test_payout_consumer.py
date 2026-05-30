from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import payout_consumer as payout_consumer_module
from banking.transactions.runtime.consumers.payout_consumer import PayoutConsumer
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


class _FakeExecutor:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.payout_provider = SimpleNamespace(provider_name="flutterwave")
        self.calls: list[dict] = []

    async def handle_payout(self, payload: dict) -> dict:
        self.calls.append(payload)
        self.payload = payload
        return self.result


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if transfer_id == str(self.transfer.id) else None

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
        return self.steps

    async def update_status(self, step_id: str, status: str) -> None:
        self.status_updates.append((step_id, status))
        for step in self.steps:
            if str(step.id) == step_id:
                step.status = status


class _FakeUnitOfWork:
    def __init__(
        self,
        transfer: SimpleNamespace,
        tx: SimpleNamespace | None,
        steps: list[SimpleNamespace] | None = None,
    ) -> None:
        self.db = _FakeDb()
        self.funded_transfers = _FakeFundedTransfers(transfer)
        self.transactions = _FakeTransactions(tx)
        self.funding_steps = _FakeFundingSteps(steps or [])
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


def _transfer() -> SimpleNamespace:
    return SimpleNamespace(
        id="funded-1",
        idempotency_key="idem-1",
        payout_initiated_at=None,
        payout_provider=None,
        payout_reference=None,
        completed_at=None,
        status=FundedTransferStatusEnum.PAYOUT_PENDING.value,
        error_message=None,
    )


def _transaction() -> SimpleNamespace:
    return SimpleNamespace(
        idempotency_key="idem-1",
        status=TransactionStatusEnum.PROCESSING.value,
        transaction_id=None,
        provider_status=None,
        provider_response=None,
        completed_at=None,
        error_message=None,
    )


@pytest.mark.asyncio
async def test_payout_consumer_completes_only_terminal_success(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    uow = _FakeUnitOfWork(transfer, tx)
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    consumer = PayoutConsumer(
        payout_executor=_FakeExecutor(
            {
                "success": True,
                "status": "successful",
                "transaction_id": "trf-1",
                "reference": "idem-1",
            }
        )
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert uow.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None),
        ("funded-1", FundedTransferStatusEnum.COMPLETED.value, None)
    ]
    assert transfer.completed_at is not None
    assert tx.status == TransactionStatusEnum.SUCCESSFUL.value
    assert tx.transaction_id == "trf-1"
    assert uow.commit_calls == 2


@pytest.mark.asyncio
async def test_payout_consumer_keeps_pending_payout_processing(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    uow = _FakeUnitOfWork(transfer, tx)
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    publisher = _CapturePublisher()
    consumer = PayoutConsumer(
        payout_executor=_FakeExecutor(
            {
                "success": False,
                "status": "pending",
                "transaction_id": "trf-2",
                "reference": "idem-1",
            }
        ),
        publisher=publisher,
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert uow.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None),
        ("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None)
    ]
    assert transfer.completed_at is None
    assert tx.status == TransactionStatusEnum.PROCESSING.value
    assert tx.provider_status == "pending"
    assert publisher.published == []
    assert uow.funding_steps.status_updates == []


@pytest.mark.asyncio
async def test_payout_consumer_failed_payout_queues_refunds(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    step = SimpleNamespace(
        id="step-1",
        amount=2500,
        account_id="account-1",
        provider_reference="mono-ref-1",
        status=FundingStepStatusEnum.CONFIRMED.value,
    )
    uow = _FakeUnitOfWork(transfer, tx, steps=[step])
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    publisher = _CapturePublisher()
    consumer = PayoutConsumer(
        payout_executor=_FakeExecutor(
            {
                "success": False,
                "status": "failed",
                "transaction_id": "trf-3",
                "reference": "idem-1",
                "error": "Invalid recipient",
            }
        ),
        publisher=publisher,
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert uow.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None),
        ("funded-1", FundedTransferStatusEnum.REFUNDING.value, "Invalid recipient")
    ]
    assert tx.status == TransactionStatusEnum.FAILED.value
    assert tx.error_message == "Invalid recipient"
    assert publisher.published == [
        (
            "refund.process",
            {
                "funding_step_id": "step-1",
                "funded_transfer_id": "funded-1",
                "amount": "2500.00",
                "amount_naira": "2500.00",
                "account_id": "account-1",
                "original_reference": "mono-ref-1",
            },
        )
    ]
    assert uow.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUND_PENDING.value)]


@pytest.mark.asyncio
async def test_payout_consumer_failed_payout_queues_all_confirmed_funding_steps(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    steps = [
        SimpleNamespace(
            id="step-1",
            amount=2500,
            account_id="account-1",
            provider_reference="mono-ref-1",
            status=FundingStepStatusEnum.CONFIRMED.value,
        ),
        SimpleNamespace(
            id="step-2",
            amount=3000,
            account_id="account-2",
            provider_reference="mono-ref-2",
            status=FundingStepStatusEnum.CONFIRMED.value,
        ),
    ]
    uow = _FakeUnitOfWork(transfer, tx, steps=steps)
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    publisher = _CapturePublisher()
    consumer = PayoutConsumer(
        payout_executor=_FakeExecutor(
            {
                "success": False,
                "status": "failed",
                "transaction_id": "trf-3",
                "reference": "idem-1",
                "error": "Invalid recipient",
            }
        ),
        publisher=publisher,
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert publisher.published == [
        (
            "refund.process",
            {
                "funding_step_id": "step-1",
                "funded_transfer_id": "funded-1",
                "amount": "2500.00",
                "amount_naira": "2500.00",
                "account_id": "account-1",
                "original_reference": "mono-ref-1",
            },
        ),
        (
            "refund.process",
            {
                "funding_step_id": "step-2",
                "funded_transfer_id": "funded-1",
                "amount": "3000.00",
                "amount_naira": "3000.00",
                "account_id": "account-2",
                "original_reference": "mono-ref-2",
            },
        ),
    ]
    assert uow.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.REFUND_PENDING.value),
        ("step-2", FundingStepStatusEnum.REFUND_PENDING.value),
    ]


@pytest.mark.asyncio
async def test_payout_consumer_failed_payout_without_publisher_commits_recoverable_refund_state(monkeypatch) -> None:
    transfer = _transfer()
    tx = _transaction()
    step = SimpleNamespace(
        id="step-1",
        amount=2500,
        account_id="account-1",
        provider_reference="mono-ref-1",
        status=FundingStepStatusEnum.CONFIRMED.value,
    )
    uow = _FakeUnitOfWork(transfer, tx, steps=[step])
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    consumer = PayoutConsumer(
        payout_executor=_FakeExecutor(
            {
                "success": False,
                "status": "failed",
                "transaction_id": "trf-3",
                "reference": "idem-1",
                "error": "Invalid recipient",
            }
        )
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert uow.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None),
        ("funded-1", FundedTransferStatusEnum.REFUNDING.value, "Invalid recipient"),
    ]
    assert uow.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUND_PENDING.value)]
    assert tx.status == TransactionStatusEnum.FAILED.value
    assert uow.commit_calls == 2


@pytest.mark.asyncio
async def test_duplicate_payout_process_skips_already_claimed_transfer(monkeypatch) -> None:
    transfer = _transfer()
    transfer.payout_initiated_at = "now"
    tx = _transaction()
    uow = _FakeUnitOfWork(transfer, tx)
    monkeypatch.setattr(payout_consumer_module, "UnitOfWork", lambda: uow)
    executor = _FakeExecutor({"success": True, "status": "successful", "transaction_id": "trf-1"})
    consumer = PayoutConsumer(payout_executor=executor)

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert executor.calls == []
    assert uow.funded_transfers.status_updates == []
    assert uow.commit_calls == 0
