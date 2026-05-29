from types import SimpleNamespace

import pytest

from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from banking.transactions.runtime.consumers import refund_consumer as refund_consumer_module
from banking.transactions.runtime.consumers.refund_consumer import RefundConsumer


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
    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str]] = []

    async def update_status(self, transfer_id: str, status: str) -> None:
        self.status_updates.append((transfer_id, status))


class _FakeUnitOfWork:
    def __init__(self, step: SimpleNamespace, all_steps: list[SimpleNamespace] | None = None) -> None:
        self.funding_steps = _FakeFundingSteps(step, all_steps)
        self.funded_transfers = _FakeFundedTransfers()
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


@pytest.mark.asyncio
async def test_refund_consumer_uses_provider_reference_for_mono_refund(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference="pool-ref-1",
        provider_debit_id="debit-1",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED, reference="pool-ref-1"))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == [("pool-ref-1", "Funding refund")]
    assert uow.funding_steps.status_updates == [("step-1", FundingStepStatusEnum.REFUNDED.value, None)]
    assert uow.funded_transfers.status_updates == [("funded-1", FundedTransferStatusEnum.REFUNDED.value)]
    assert uow.commit_calls == 1


@pytest.mark.asyncio
async def test_refund_consumer_falls_back_to_original_reference_payload(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference=None,
        provider_debit_id="debit-1",
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
async def test_refund_consumer_keeps_step_pending_when_provider_reference_missing(monkeypatch) -> None:
    step = SimpleNamespace(
        id="step-1",
        status=FundingStepStatusEnum.REFUND_PENDING.value,
        provider_reference=None,
        provider_debit_id="debit-1",
    )
    uow = _FakeUnitOfWork(step)
    monkeypatch.setattr(refund_consumer_module, "UnitOfWork", lambda: uow)
    provider = _RefundProvider(DebitResult(success=True, status=DebitStatus.REVERSED))
    consumer = RefundConsumer(direct_debit_provider=provider)  # type: ignore[arg-type]

    await consumer.process_job({"funding_step_id": "step-1", "funded_transfer_id": "funded-1"})

    assert provider.calls == []
    assert uow.funding_steps.status_updates == [
        ("step-1", FundingStepStatusEnum.REFUND_PENDING.value, "Provider reference missing for refund")
    ]
