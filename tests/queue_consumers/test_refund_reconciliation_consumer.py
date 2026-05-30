from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import refund_reconciliation_consumer as reconciliation_module
from banking.transactions.runtime.consumers.refund_reconciliation_consumer import RefundReconciliationConsumer
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum


class _FakeProvider:
    def __init__(self, result: DebitResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    async def get_refund_status(self, debit_reference: str, refund_id: str | None = None) -> DebitResult:
        self.calls.append((debit_reference, refund_id))
        return self.result


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


class _FakeFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps
        self.status_updates: list[tuple[str, str, str | None]] = []
        self.stale_calls: list[dict] = []

    async def get_by_id(self, step_id: str) -> SimpleNamespace | None:
        return next((step for step in self.steps if str(step.id) == step_id), None)

    async def get_stale_refunds(self, *, cutoff, limit: int) -> list[SimpleNamespace]:
        self.stale_calls.append({"cutoff": cutoff, "limit": limit})
        return [
            step
            for step in self.steps
            if step.status
            in (FundingStepStatusEnum.REFUND_PENDING.value, FundingStepStatusEnum.REFUND_PROCESSING.value)
        ]

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
        step = await self.get_by_id(step_id)
        if step:
            step.status = status

    async def get_by_transfer(self, funded_transfer_id: str) -> list[SimpleNamespace]:
        return [step for step in self.steps if str(step.funded_transfer_id) == funded_transfer_id]


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if str(self.transfer.id) == transfer_id else None

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        self.status_updates.append((transfer_id, status, error_message))
        self.transfer.status = status
        self.transfer.error_message = error_message


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace) -> None:
        self.tx = tx

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return self.tx if self.tx.idempotency_key == idempotency_key else None


class _FakeSupportTickets:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def get_by_transaction_ref(self, transaction_ref: str) -> list[SimpleNamespace]:
        del transaction_ref
        return []

    async def generate_ticket_code(self) -> str:
        return "SUP-20260529-0001"

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(**kwargs)


class _SharedState:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.db = _FakeDb()
        self.transfer = SimpleNamespace(
            id="funded-1",
            user_id="user-1",
            idempotency_key="idem-1",
            status=FundedTransferStatusEnum.REFUNDING.value,
            error_message=None,
        )
        self.tx = SimpleNamespace(
            idempotency_key="idem-1",
            status=TransactionStatusEnum.FAILED.value,
            provider_status=None,
            completed_at=None,
            error_message=None,
        )
        self.funding_steps = _FakeFundingSteps(steps)
        self.funded_transfers = _FakeFundedTransfers(self.transfer)
        self.transactions = _FakeTransactions(self.tx)
        self.support_tickets = _FakeSupportTickets()
        self.commit_calls = 0


class _FakeUnitOfWork:
    def __init__(self, state: _SharedState) -> None:
        self.db = state.db
        self.funding_steps = state.funding_steps
        self.funded_transfers = state.funded_transfers
        self.transactions = state.transactions
        self.support_tickets = state.support_tickets
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self._state.commit_calls += 1


def _refund_step(status: str = FundingStepStatusEnum.REFUND_PROCESSING.value) -> SimpleNamespace:
    return SimpleNamespace(
        id="step-1",
        funded_transfer_id="funded-1",
        account_id="account-1",
        amount=2500,
        status=status,
        provider_reference="pool-ref-1",
        refund_provider_id="refund-1",
        refund_provider_reference="pool-ref-1",
        refund_initiated_at="now",
        refund_attempt_count=0,
        refund_last_checked_at=None,
        confirmed_at="now",
    )


@pytest.mark.asyncio
async def test_refund_reconciliation_marks_transaction_reversed_after_all_refunded(monkeypatch) -> None:
    step = _refund_step()
    failed_step = SimpleNamespace(
        id="step-2",
        funded_transfer_id="funded-1",
        status=FundingStepStatusEnum.FAILED.value,
        provider_reference="pool-ref-2",
        confirmed_at=None,
    )
    state = _SharedState([step, failed_step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    consumer = RefundReconciliationConsumer(
        direct_debit_provider=_FakeProvider(
            DebitResult(success=True, status=DebitStatus.REVERSED, debit_id="refund-1", reference="pool-ref-1")
        )
    )

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert state.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUNDED.value, None)]
    assert state.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.REFUNDED.value, None)]
    assert state.tx.status == TransactionStatusEnum.REVERSED.value
    assert state.commit_calls == 1


@pytest.mark.asyncio
async def test_refund_reconciliation_keeps_ambiguous_status_pending(monkeypatch) -> None:
    step = _refund_step()
    state = _SharedState([step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(DebitResult(success=True, status=DebitStatus.PENDING, reference="pool-ref-1"))
    consumer = RefundReconciliationConsumer(direct_debit_provider=provider)

    await consumer.process_job({"funding_step_id": "step-1"})

    assert provider.calls == [("pool-ref-1", "refund-1")]
    assert state.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.REFUND_PROCESSING.value, "Refund still pending")
    ]
    assert state.funded_transfers.status_updates == []


@pytest.mark.asyncio
async def test_refund_reconciliation_failed_status_records_manual_review(monkeypatch) -> None:
    step = _refund_step()
    state = _SharedState([step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    consumer = RefundReconciliationConsumer(
        direct_debit_provider=_FakeProvider(
            DebitResult(success=False, status=DebitStatus.FAILED, reference="pool-ref-1", error_message="Rejected")
        )
    )

    await consumer.process_job({"funding_step_id": "step-1"})

    assert state.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUND_FAILED.value, "Rejected")]
    assert state.funded_transfers.status_updates == [
        ("funded-1", FundedTransferStatusEnum.FAILED.value, "Refund failed; manual review required")
    ]
    assert state.tx.status == TransactionStatusEnum.FAILED.value
    assert state.support_tickets.created[0]["intent"] == "reversal_refund"


@pytest.mark.asyncio
async def test_refund_reconciliation_requeues_unclaimed_pending_refund(monkeypatch) -> None:
    step = _refund_step(FundingStepStatusEnum.REFUND_PENDING.value)
    step.refund_provider_id = None
    step.refund_provider_reference = None
    step.refund_initiated_at = None
    state = _SharedState([step])
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    provider = _FakeProvider(DebitResult(success=True, status=DebitStatus.REVERSED, reference="pool-ref-1"))
    publisher = _CapturePublisher()
    consumer = RefundReconciliationConsumer(direct_debit_provider=provider, publisher=publisher)

    await consumer.process_job({"funding_step_id": "step-1"})

    assert provider.calls == []
    assert publisher.published == [
        (
            "refund.process",
            {
                "funding_step_id": "step-1",
                "funded_transfer_id": "funded-1",
                "amount": "2500.00",
                "amount_naira": "2500.00",
                "account_id": "account-1",
                "original_reference": "pool-ref-1",
            },
        )
    ]
    assert state.funding_steps.status_updates == []
