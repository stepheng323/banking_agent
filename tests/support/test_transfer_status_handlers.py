from __future__ import annotations

import pytest

from apps.core.src.agent.graphs.support.handlers.failure import handle_failure_reason
from apps.core.src.agent.graphs.support.handlers.retry import handle_retry
from apps.core.src.agent.graphs.support.handlers.status import handle_pending, handle_transfer_status


@pytest.mark.asyncio
async def test_transfer_status_handler_accepts_successful_status() -> None:
    response = await handle_transfer_status(
        {"status": "successful", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert "Mum" in response.message
    assert response.offer_receipt is True


@pytest.mark.asyncio
async def test_pending_handler_treats_processing_as_pending() -> None:
    response = await handle_pending(
        {"status": "processing", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert "processed" in response.message.lower() or "processing" in response.message.lower()


@pytest.mark.asyncio
async def test_failure_reason_uses_response_code_context_for_failed_status() -> None:
    response = await handle_failure_reason(
        {
            "status": "failed",
            "amount": 5000,
            "provider_response": {"response_code": "51", "reason": "Insufficient funds"},
        },
        locale="en",
    )

    assert "Insufficient funds" in response.message


@pytest.mark.asyncio
async def test_retry_handler_accepts_successful_status() -> None:
    response = await handle_retry(
        {"status": "successful", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert response.offer_retry is True
