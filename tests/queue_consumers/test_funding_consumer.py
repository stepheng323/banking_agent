from decimal import Decimal
from types import SimpleNamespace

import pytest

from banking.transactions.runtime.consumers import funding_consumer as funding_consumer_module
from banking.transactions.runtime.consumers.funding_consumer import FundingConsumer
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


class _NoopDirectDebitProvider:
    pass


class _DebitProvider:
    def __init__(self, result: DebitResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Decimal, str, str]] = []

    async def initiate_pooling_debit(
        self,
        mandate_id: str,
        amount: Decimal,
        reference: str,
        narration: str = "Transfer",
    ) -> DebitResult:
        self.calls.append((mandate_id, amount, reference, narration))
        return self.result


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


class _FakeFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return self.steps

    async def all_confirmed(self, transfer_id: str) -> bool:
        del transfer_id
        return True


class _ClaimingFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return self.steps

    async def get_by_id(self, step_id: str) -> SimpleNamespace | None:
        return next((step for step in self.steps if str(step.id) == step_id), None)

    async def get_by_id_for_update(self, step_id: str) -> SimpleNamespace | None:
        return await self.get_by_id(step_id)

    async def all_confirmed(self, transfer_id: str) -> bool:
        del transfer_id
        return all(step.status == FundingStepStatusEnum.CONFIRMED.value for step in self.steps)

    async def get_confirmed_for_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return [step for step in self.steps if step.status == FundingStepStatusEnum.CONFIRMED.value]

    async def claim_for_debit(self, step_id: str, *, provider_reference: str) -> SimpleNamespace | None:
        step = await self.get_by_id(step_id)
        if not step or step.status != FundingStepStatusEnum.PENDING.value:
            return None
        step.status = FundingStepStatusEnum.PROCESSING.value
        step.provider_reference = provider_reference
        step.initiated_at = "now"
        return step

    async def update_status(
        self,
        step_id: str,
        status: str,
        provider_reference: str | None = None,
        provider_debit_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.status_updates.append((step_id, status, error_message))
        step = await self.get_by_id(step_id)
        if step:
            step.status = status
            if provider_reference:
                step.provider_reference = provider_reference
            if provider_debit_id:
                step.provider_debit_id = provider_debit_id
            if error_message:
                step.error_message = error_message


class _FakeAccounts:
    async def get_by_id(self, account_id: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=account_id, mandate_id="mandate-1")


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


class _FakeUnitOfWork:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.funded_transfers = _FakeFundedTransfers(transfer)
        self.funding_steps = _FakeFundingSteps(
            [SimpleNamespace(id="step-1", status=FundingStepStatusEnum.CONFIRMED.value)]
        )
        self.accounts = SimpleNamespace()
        self.ledger_accounts = _FakeLedgerAccounts()
        self.ledger_entries = _FakeLedgerEntries()
        self.transactions = None
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


class _ClaimingUnitOfWork:
    def __init__(self, transfer: SimpleNamespace, steps: list[SimpleNamespace]) -> None:
        self.funded_transfers = _FakeFundedTransfers(transfer)
        self.funding_steps = _ClaimingFundingSteps(steps)
        self.accounts = _FakeAccounts()
        self.ledger_accounts = _FakeLedgerAccounts()
        self.ledger_entries = _FakeLedgerEntries()
        self.transactions = None
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_funding_consumer_publishes_payout_provider_metadata(monkeypatch) -> None:
    transfer = SimpleNamespace(
        id="funded-1",
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="000014",
        payout_provider="flutterwave",
        idempotency_key="idem-1",
        narration="Test",
        funding_completed_at=None,
        status=FundedTransferStatusEnum.FUNDING_PENDING.value,
    )
    uow = _FakeUnitOfWork(transfer)
    monkeypatch.setattr(funding_consumer_module, "UnitOfWork", lambda: uow)
    publisher = _CapturePublisher()
    consumer = FundingConsumer(
        publisher=publisher,
        direct_debit_provider=_NoopDirectDebitProvider(),  # type: ignore[arg-type]
    )

    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert uow.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value)]
    assert publisher.published == [
        (
            "payout.process",
            {
                "funded_transfer_id": "funded-1",
                "amount": "5000.00",
                "amount_naira": "5000.00",
                "recipient_account": "8162511023",
                "recipient_bank_code": "000014",
                "recipient_bank_code_provider": "flutterwave",
                "recipient_resolution_provider": "flutterwave",
                "payout_provider": "flutterwave",
                "idempotency_key": "idem-1",
                "narration": "Test",
            },
        )
    ]
    assert uow.commit_calls == 1


@pytest.mark.asyncio
async def test_duplicate_funding_process_claims_step_once(monkeypatch) -> None:
    transfer = SimpleNamespace(
        id="funded-1",
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="000014",
        payout_provider="flutterwave",
        idempotency_key="idem-1",
        narration="Test",
        funding_completed_at=None,
        status=FundedTransferStatusEnum.FUNDING_PENDING.value,
    )
    steps = [
        SimpleNamespace(
            id="step-1",
            funded_transfer_id="funded-1",
            account_id="account-1",
            amount=5000,
            sequence=1,
            status=FundingStepStatusEnum.PENDING.value,
            provider_reference=None,
            provider_debit_id=None,
            retry_count=0,
            initiated_at=None,
        )
    ]
    uow = _ClaimingUnitOfWork(transfer, steps)
    monkeypatch.setattr(funding_consumer_module, "UnitOfWork", lambda: uow)
    publisher = _CapturePublisher()
    provider = _DebitProvider(
        DebitResult(
            success=True,
            status=DebitStatus.SUCCESSFUL,
            debit_id="debit-1",
            reference="idem-1-s1",
        )
    )
    consumer = FundingConsumer(publisher=publisher, direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funded_transfer_id": "funded-1"})
    await consumer.process_job({"funded_transfer_id": "funded-1"})

    assert provider.calls == [("mandate-1", Decimal("5000.00"), "idem-1-s1", "Test")]
    assert uow.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.CONFIRMED.value, None),
    ]
    assert uow.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.PAYOUT_PENDING.value)]
    assert [topic for topic, _ in publisher.published] == ["payout.process"]
