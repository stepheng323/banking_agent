from decimal import Decimal
from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import funding_reconciliation_consumer as reconciliation_module
from banking.transactions.runtime.consumers.funding_reconciliation_consumer import FundingReconciliationConsumer
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum, TransactionStatusEnum


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


class _FakeProvider:
    def __init__(self, result: DebitResult) -> None:
        self.result = result
        self.status_calls: list[str] = []
        self.initiate_calls: list[tuple[str, Decimal, str, str]] = []

    async def get_debit_status(self, debit_id: str) -> DebitResult:
        self.status_calls.append(debit_id)
        return self.result

    async def initiate_pooling_debit(
        self,
        mandate_id: str,
        amount: Decimal,
        reference: str,
        narration: str = "Transfer",
    ) -> DebitResult:
        self.initiate_calls.append((mandate_id, amount, reference, narration))
        return self.result


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


class _FakeFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_id(self, step_id: str) -> SimpleNamespace | None:
        return next((step for step in self.steps if str(step.id) == step_id), None)

    async def get_stale_processing(self, *, cutoff, limit: int) -> list[SimpleNamespace]:
        del cutoff, limit
        return [step for step in self.steps if step.status == FundingStepStatusEnum.PROCESSING.value]

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

    async def all_confirmed(self, funded_transfer_id: str) -> bool:
        steps = await self.get_by_transfer(funded_transfer_id)
        return all(step.status == FundingStepStatusEnum.CONFIRMED.value for step in steps)

    async def get_confirmed_for_transfer(self, funded_transfer_id: str) -> list[SimpleNamespace]:
        return [
            step
            for step in await self.get_by_transfer(funded_transfer_id)
            if step.status == FundingStepStatusEnum.CONFIRMED.value
        ]


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if transfer_id == str(self.transfer.id) else None

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        self.status_updates.append((transfer_id, status, error_message))
        self.transfer.status = status
        self.transfer.error_message = error_message


class _FakeAccounts:
    async def get_by_id(self, account_id: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=account_id, mandate_id="mandate-1")


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace) -> None:
        self.tx = tx

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        return self.tx if self.tx.idempotency_key == idempotency_key else None


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
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.db = _FakeDb()
        self.transfer = SimpleNamespace(
            id="funded-1",
            amount=5000,
            recipient_account_number="1234567890",
            recipient_bank_code="000014",
            payout_provider="flutterwave",
            idempotency_key="idem-1",
            narration="Test",
            status=FundedTransferStatusEnum.FUNDING_PENDING.value,
            funding_completed_at=None,
            error_message=None,
        )
        self.tx = SimpleNamespace(
            idempotency_key="idem-1",
            status=TransactionStatusEnum.PROCESSING.value,
            error_message=None,
            provider_status=None,
        )
        self.funding_steps = _FakeFundingSteps(steps)
        self.funded_transfers = _FakeFundedTransfers(self.transfer)
        self.accounts = _FakeAccounts()
        self.transactions = _FakeTransactions(self.tx)
        self.ledger_accounts = _FakeLedgerAccounts()
        self.ledger_entries = _FakeLedgerEntries()
        self.commit_calls = 0


class _FakeUnitOfWork:
    def __init__(self, state: _SharedState) -> None:
        self.db = state.db
        self.funding_steps = state.funding_steps
        self.funded_transfers = state.funded_transfers
        self.accounts = state.accounts
        self.transactions = state.transactions
        self.ledger_accounts = state.ledger_accounts
        self.ledger_entries = state.ledger_entries
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self._state.commit_calls += 1


def _step(
    step_id: str,
    *,
    status: str,
    sequence: int,
    provider_debit_id: str | None = None,
    provider_reference: str | None = None,
    retry_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=step_id,
        funded_transfer_id="funded-1",
        account_id=f"account-{sequence}",
        amount=2500,
        sequence=sequence,
        status=status,
        provider_debit_id=provider_debit_id,
        provider_reference=provider_reference,
        retry_count=retry_count,
    )


@pytest.mark.asyncio
async def test_funding_reconciliation_success_queues_payout_once(monkeypatch) -> None:
    steps = [
        _step("step-1", status=FundingStepStatusEnum.PROCESSING.value, sequence=1, provider_debit_id="debit-1"),
        _step("step-2", status=FundingStepStatusEnum.CONFIRMED.value, sequence=2, provider_reference="ref-2"),
    ]
    state = _SharedState(steps)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    publisher = _CapturePublisher()
    consumer = FundingReconciliationConsumer(
        direct_debit_provider=_FakeProvider(
            DebitResult(success=True, status=DebitStatus.SUCCESSFUL, debit_id="debit-1", reference="ref-1")
        ),
        publisher=publisher,
    )

    await consumer.process_job({"funding_step_id": "step-1"})

    assert state.funding_steps.status_updates[0] == ("step-1", FundingStepStatusEnum.CONFIRMED.value, None)
    assert state.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value, None)]
    assert publisher.published[0][0] == "payout.process"


@pytest.mark.asyncio
async def test_funding_reconciliation_terminal_failure_refunds_confirmed_leg(monkeypatch) -> None:
    steps = [
        _step("step-1", status=FundingStepStatusEnum.CONFIRMED.value, sequence=1, provider_reference="ref-1"),
        _step("step-2", status=FundingStepStatusEnum.PROCESSING.value, sequence=2, provider_debit_id="debit-2"),
    ]
    state = _SharedState(steps)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    publisher = _CapturePublisher()
    consumer = FundingReconciliationConsumer(
        direct_debit_provider=_FakeProvider(
            DebitResult(success=False, status=DebitStatus.FAILED, debit_id="debit-2", error_message="Declined")
        ),
        publisher=publisher,
    )

    await consumer.process_job({"funding_step_id": "step-2"})

    assert ("step-2", FundingStepStatusEnum.FAILED.value, "Declined") in state.funding_steps.status_updates
    assert publisher.published == [
        (
            "refund.process",
            {
                "funding_step_id": "step-1",
                "funded_transfer_id": "funded-1",
                "amount": "2500.00",
                "amount_naira": "2500.00",
                "account_id": "account-1",
                "original_reference": "ref-1",
            },
        )
    ]
    assert state.tx.status == TransactionStatusEnum.FAILED.value


@pytest.mark.asyncio
async def test_funding_reconciliation_recovers_missed_pending_funding_job(monkeypatch) -> None:
    steps = [
        _step("step-1", status=FundingStepStatusEnum.PENDING.value, sequence=1),
    ]
    state = _SharedState(steps)
    monkeypatch.setattr(reconciliation_module, "UnitOfWork", lambda: _FakeUnitOfWork(state))
    publisher = _CapturePublisher()
    provider = _FakeProvider(
        DebitResult(success=True, status=DebitStatus.SUCCESSFUL, debit_id="debit-1", reference="idem-1-s1")
    )
    consumer = FundingReconciliationConsumer(direct_debit_provider=provider, publisher=publisher)

    await consumer.process_job({"funding_step_id": "step-1"})

    assert provider.initiate_calls == [("mandate-1", Decimal("2500.00"), "idem-1-s1", "Test")]
    assert state.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.PROCESSING.value, None),
        ("step-1", FundingStepStatusEnum.CONFIRMED.value, None),
    ]
    assert publisher.published[0][0] == "payout.process"
