from __future__ import annotations

import pytest

from apps.chat.src.agent.graphs.support.handlers.failure import handle_failure_reason
from apps.chat.src.agent.graphs.support.handlers.retry import handle_retry
from apps.chat.src.agent.graphs.support.handlers.status import handle_pending, handle_transfer_status


@pytest.mark.asyncio
async def test_transfer_status_handler_accepts_successful_status() -> None:
    response = await handle_transfer_status(
        {"status": "successful", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert "Mum" in response.message
    assert response.offer_receipt is True


@pytest.mark.asyncio
async def test_transfer_status_handler_uses_shared_posted_status_copy() -> None:
    response = await handle_transfer_status(
        {"status": "posted", "bank_status": "posted", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert response.message == "That transaction is posted."


@pytest.mark.asyncio
async def test_pending_handler_treats_processing_as_pending() -> None:
    response = await handle_pending(
        {"status": "processing", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert "processed" in response.message.lower() or "processing" in response.message.lower()


@pytest.mark.asyncio
async def test_failure_reason_uses_shared_processing_bank_posted_copy() -> None:
    response = await handle_failure_reason(
        {
            "status": "processing",
            "local_status": "processing",
            "bank_status": "posted",
            "amount": 5000,
            "recipient_name": "Mum",
        },
        locale="en",
    )

    assert (
        response.message
        == "That transaction is still processing in our app, but a matching debit is posted in your bank history."
    )


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
async def test_failure_reason_adds_category_specific_repair_guidance() -> None:
    response = await handle_failure_reason(
        {
            "status": "failed",
            "amount": 5000,
            "error_message": "Provider down",
            "failure_category": "provider_unavailable",
            "provider_response": {},
        },
        locale="en",
    )

    assert "Provider down" in response.message
    assert "retry now" in response.message.lower()


@pytest.mark.parametrize(
    ("locale", "expected_guidance"),
    [
        ("en", "another source account"),
        ("pcm", "before you retry"),
        ("yo", "Lo source account miran"),
        ("ha", "Yi amfani da wani source account"),
        ("ig", "Jiri source account ọzọ"),
    ],
)
@pytest.mark.asyncio
async def test_failure_reason_uses_catalog_guidance_for_supported_locales(
    locale: str, expected_guidance: str
) -> None:
    response = await handle_failure_reason(
        {
            "status": "failed",
            "amount": 5000,
            "error_message": "Insufficient funds",
            "failure_category": "insufficient_funds",
            "provider_response": {},
        },
        locale=locale,
    )

    assert "Insufficient funds" in response.message
    assert expected_guidance in response.message


@pytest.mark.asyncio
async def test_retry_handler_accepts_successful_status() -> None:
    response = await handle_retry(
        {"status": "successful", "amount": 5000, "recipient_name": "Mum"},
        locale="en",
    )

    assert response.offer_retry is True


@pytest.mark.asyncio
async def test_retry_handler_uses_failure_category_for_repair_prompt() -> None:
    response = await handle_retry(
        {
            "status": "failed",
            "amount": 5000,
            "recipient_name": "Mum",
            "error_message": "Insufficient funds",
            "failure_category": "insufficient_funds",
        },
        locale="en",
    )

    assert response.offer_retry is False
    assert "another source account" in response.message.lower()


@pytest.mark.parametrize(
    ("locale", "expected_guidance"),
    [
        ("en", "recipient account"),
        ("pcm", "before you retry"),
        ("yo", "Ṣatunṣe recipient account"),
        ("ha", "Gyara recipient account"),
        ("ig", "Dozie recipient account"),
    ],
)
@pytest.mark.asyncio
async def test_retry_handler_uses_catalog_block_guidance_for_supported_locales(
    locale: str, expected_guidance: str
) -> None:
    response = await handle_retry(
        {
            "status": "failed",
            "amount": 5000,
            "recipient_name": "Mum",
            "error_message": "Invalid account details",
            "failure_category": "validation_error",
        },
        locale=locale,
    )

    assert response.offer_retry is False
    assert expected_guidance in response.message
