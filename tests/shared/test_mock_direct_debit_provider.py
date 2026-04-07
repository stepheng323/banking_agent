from __future__ import annotations

import pytest

from shared.clients.abstractions.direct_debit import DebitStatus
from shared.clients.providers.mock.direct_debit import MockDirectDebitProvider


@pytest.mark.asyncio
async def test_mock_provider_initiate_returns_successful_with_terminal_code() -> None:
    provider = MockDirectDebitProvider()

    result = await provider.initiate_debit(
        mandate_id="mandate-1",
        amount=5000,
        reference="ref-1",
        narration="Allowance",
        beneficiary_account="8162511023",
        beneficiary_bank_code="100004",
    )

    assert result.success is True
    assert result.status == DebitStatus.SUCCESSFUL
    assert result.provider_response is not None
    assert result.provider_response["status"] == "successful"
    assert result.provider_response["response_code"] == "00"
    assert result.provider_response["debit_type"] == "direct-to-beneficiary"


@pytest.mark.asyncio
async def test_mock_provider_status_progresses_to_successful_with_00_code() -> None:
    provider = MockDirectDebitProvider()
    initiated = await provider.initiate_debit(
        mandate_id="mandate-1",
        amount=5000,
        reference="ref-1",
        narration="Allowance",
    )
    provider._debits["ref-1"]["status"] = DebitStatus.PENDING.value
    provider._debits["ref-1"].pop("response_code", None)

    first_poll = await provider.get_debit_status(str(initiated.debit_id))
    second_poll = await provider.get_debit_status(str(initiated.debit_id))

    assert first_poll.success is True
    assert first_poll.status == DebitStatus.PROCESSING
    assert first_poll.provider_response is not None
    assert first_poll.provider_response["status"] == "processing"
    assert "response_code" not in first_poll.provider_response

    assert second_poll.success is True
    assert second_poll.status == DebitStatus.SUCCESSFUL
    assert second_poll.provider_response is not None
    assert second_poll.provider_response["status"] == "successful"
    assert second_poll.provider_response["response_code"] == "00"


@pytest.mark.asyncio
async def test_mock_provider_simulated_failure_returns_failed_with_code() -> None:
    provider = MockDirectDebitProvider()
    initiated = await provider.initiate_debit(
        mandate_id="mandate-1",
        amount=5000,
        reference="ref-failed",
    )
    provider.simulate_debit_failure("ref-failed")

    failed = await provider.get_debit_status(str(initiated.debit_id))

    assert failed.success is False
    assert failed.status == DebitStatus.FAILED
    assert failed.error_message == "Debit failed"
    assert failed.provider_response is not None
    assert failed.provider_response["response_code"] == "51"
    assert failed.provider_response["status"] == "failed"
