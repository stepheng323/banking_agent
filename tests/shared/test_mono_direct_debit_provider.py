from __future__ import annotations

import pytest

from shared.clients.abstractions.direct_debit import DebitStatus
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider


class _MonoClientStub:
    def __init__(
        self,
        *,
        initiate_response: dict | None = None,
        status_response: dict | None = None,
        refund_response: dict | None = None,
        verify_response: dict | None = None,
    ) -> None:
        self._initiate_response = initiate_response or {}
        self._status_response = status_response or {}
        self._refund_response = refund_response or {}
        self._verify_response = verify_response or {}
        self.initiate_calls: list[dict] = []
        self.refund_calls: list[tuple[str, str | None]] = []
        self.verify_calls: list[str] = []

    async def initiate_debit(
        self,
        mandate_id: str,
        amount: int,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> dict:
        self.initiate_calls.append(
            {
                "mandate_id": mandate_id,
                "amount": amount,
                "reference": reference,
                "narration": narration,
                "beneficiary_account": beneficiary_account,
                "beneficiary_bank_code": beneficiary_bank_code,
            }
        )
        return dict(self._initiate_response)

    async def get_debit_status(self, debit_id: str) -> dict:
        return dict(self._status_response)

    async def refund_payment(self, reference: str, source: str | None = None) -> dict:
        self.refund_calls.append((reference, source))
        return dict(self._refund_response)

    async def verify_payment(self, reference: str) -> dict:
        self.verify_calls.append(reference)
        return dict(self._verify_response)


@pytest.mark.asyncio
async def test_mono_provider_success_requires_successful_status_and_00_code() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(initiate_response={"id": "debit-1", "status": "successful", "response_code": "00"})
    )

    result = await provider.initiate_debit(mandate_id="mandate-1", amount=5000, reference="ref-1")

    assert result.success is True
    assert result.status == DebitStatus.SUCCESSFUL
    assert result.error_message is None


@pytest.mark.asyncio
async def test_mono_provider_initiate_debit_sends_amount_in_kobo() -> None:
    client = _MonoClientStub(
        initiate_response={"id": "debit-1", "status": "successful", "response_code": "00", "reference": "ref-1"}
    )
    provider = MonoDirectDebitProvider(client)

    result = await provider.initiate_debit(mandate_id="mandate-1", amount="2000.05", reference="ref-1")

    assert client.initiate_calls[0]["amount"] == 200005
    assert result.amount is not None
    assert str(result.amount) == "2000.05"


@pytest.mark.asyncio
async def test_mono_provider_failed_status_with_non_zero_code_is_failure() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(
            initiate_response={
                "id": "debit-1",
                "status": "failed",
                "response_code": "51",
                "message": "Insufficient funds",
            }
        )
    )

    result = await provider.initiate_debit(mandate_id="mandate-1", amount=5000, reference="ref-1")

    assert result.success is False
    assert result.status == DebitStatus.FAILED
    assert result.error_message == "Insufficient funds"


@pytest.mark.asyncio
async def test_mono_provider_missing_response_code_falls_back_to_status_only() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(status_response={"id": "debit-1", "status": "processing", "reference": "ref-1", "amount": 500000})
    )

    result = await provider.get_debit_status("debit-1")

    assert result.success is True
    assert result.status == DebitStatus.PROCESSING
    assert result.amount is not None
    assert str(result.amount) == "5000.00"
    assert result.error_message is None


@pytest.mark.asyncio
async def test_mono_provider_successful_status_with_non_zero_code_fails_closed() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(
            status_response={
                "id": "debit-1",
                "status": "successful",
                "response_code": "96",
                "message": "System malfunction",
                "reference": "ref-1",
                "amount": 500000,
            }
        )
    )

    result = await provider.get_debit_status("debit-1")

    assert result.success is False
    assert result.status == DebitStatus.FAILED
    assert result.error_message == "System malfunction"


@pytest.mark.asyncio
async def test_mono_provider_refund_uses_payment_reference() -> None:
    client = _MonoClientStub(
        refund_response={
            "id": "refund-1",
            "reference": "pool-ref-1",
            "status": "successful",
            "response_code": "00",
        }
    )
    provider = MonoDirectDebitProvider(client)

    result = await provider.reverse_debit("pool-ref-1", reason="Funding refund")

    assert client.refund_calls == [("pool-ref-1", None)]
    assert result.success is True
    assert result.status == DebitStatus.REVERSED
    assert result.debit_id == "refund-1"
    assert result.reference == "pool-ref-1"


@pytest.mark.asyncio
async def test_mono_provider_refund_pending_stays_pending() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(refund_response={"id": "refund-1", "reference": "pool-ref-1", "status": "pending"})
    )

    result = await provider.reverse_debit("pool-ref-1")

    assert result.success is True
    assert result.status == DebitStatus.PENDING


@pytest.mark.asyncio
async def test_mono_provider_refund_failure_fails_closed() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(
            refund_response={
                "id": "refund-1",
                "reference": "pool-ref-1",
                "status": "failed",
                "message": "Refund rejected",
            }
        )
    )

    result = await provider.reverse_debit("pool-ref-1")

    assert result.success is False
    assert result.status == DebitStatus.FAILED
    assert result.error_message == "Refund rejected"


@pytest.mark.asyncio
async def test_mono_provider_refund_status_uses_payment_verification() -> None:
    client = _MonoClientStub(
        verify_response={"id": "pay-1", "reference": "pool-ref-1", "status": "reversed", "amount": "500000"}
    )
    provider = MonoDirectDebitProvider(client)

    result = await provider.get_refund_status("pool-ref-1", refund_id="refund-1")

    assert client.verify_calls == ["pool-ref-1"]
    assert result.status == DebitStatus.REVERSED
    assert result.success is True
    assert result.amount is not None
    assert str(result.amount) == "5000.00"


@pytest.mark.asyncio
async def test_mono_provider_refund_status_original_success_stays_pending() -> None:
    provider = MonoDirectDebitProvider(
        _MonoClientStub(verify_response={"id": "pay-1", "reference": "pool-ref-1", "status": "successful"})
    )

    result = await provider.get_refund_status("pool-ref-1")

    assert result.status == DebitStatus.PENDING
    assert result.success is True
