from types import SimpleNamespace

import pytest

from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from shared.transaction_runtime.consumers import funding_consumer as funding_consumer_module
from shared.transaction_runtime.consumers.funding_consumer import FundingConsumer


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


class _NoopDirectDebitProvider:
    pass


class _FakeFundedTransfers:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.transfer = transfer
        self.status_updates: list[tuple[str, str]] = []

    async def get_by_id(self, transfer_id: str) -> SimpleNamespace | None:
        return self.transfer if transfer_id == str(self.transfer.id) else None

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        del error_message
        self.status_updates.append((transfer_id, status))


class _FakeFundingSteps:
    def __init__(self, steps: list[SimpleNamespace]) -> None:
        self.steps = steps

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return self.steps

    async def all_confirmed(self, transfer_id: str) -> bool:
        del transfer_id
        return True


class _FakeUnitOfWork:
    def __init__(self, transfer: SimpleNamespace) -> None:
        self.funded_transfers = _FakeFundedTransfers(transfer)
        self.funding_steps = _FakeFundingSteps(
            [SimpleNamespace(id="step-1", status=FundingStepStatusEnum.CONFIRMED.value)]
        )
        self.accounts = SimpleNamespace()
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
                "amount": 5000.0,
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
