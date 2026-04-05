from __future__ import annotations

import pytest

from shared.clients.abstractions.direct_debit import DebitStatus
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider


class _MonoClientStub:
    def __init__(self, *, initiate_response: dict | None = None, status_response: dict | None = None) -> None:
        self._initiate_response = initiate_response or {}
        self._status_response = status_response or {}

    async def initiate_debit(
        self,
        mandate_id: str,
        amount: int,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> dict:
        return dict(self._initiate_response)

    async def get_debit_status(self, debit_id: str) -> dict:
        return dict(self._status_response)


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
