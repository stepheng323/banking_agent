from typing import Any
from unittest.mock import AsyncMock

import pytest

from shared.services.post_transaction_beneficiary import (
    append_beneficiary_suggestion,
    suggest_mobile_beneficiary,
    suggest_transfer_beneficiary,
)


class _SuggestionServiceStub:
    def __init__(self, message: str | None = "save this") -> None:
        self.check_and_suggest_beneficiary = AsyncMock(return_value=message)


async def _suggest_mobile(service: Any, **overrides: Any) -> str | None:
    params = {
        "phone_number": "2348162511023",
        "channel": "whatsapp",
        "locale": "en",
        "transaction_id": "tx-1",
        "beneficiary_type": "airtime",
        "recipient_phone": "08031234567",
        "network": "MTN",
        "recipient_name": "Tolu",
    }
    params.update(overrides)
    return await suggest_mobile_beneficiary(service, **params)


async def _suggest_transfer(service: Any, **overrides: Any) -> str | None:
    params = {
        "phone_number": "2348162511023",
        "channel": "whatsapp",
        "locale": "en",
        "transaction_id": "tx-1",
        "account_number": "2010000001",
        "bank_code": "044",
        "bank_name": "Access Bank",
        "recipient_name": "Tolu",
    }
    params.update(overrides)
    return await suggest_transfer_beneficiary(service, **params)


def test_append_beneficiary_suggestion_adds_visible_block() -> None:
    assert append_beneficiary_suggestion("Done.", "Save this beneficiary?") == (
        "Done.\n\nSave this beneficiary?"
    )
    assert append_beneficiary_suggestion("Done.", "") == "Done."
    assert append_beneficiary_suggestion("", "Save this beneficiary?") == "Save this beneficiary?"


@pytest.mark.asyncio
async def test_transfer_suggestion_skips_self_and_incomplete_recipient() -> None:
    service = _SuggestionServiceStub()

    assert await _suggest_transfer(service, is_self=True) is None
    assert await _suggest_transfer(service, account_number="") is None
    assert await _suggest_transfer(service, bank_code="", bank_name="") is None

    service.check_and_suggest_beneficiary.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_suggestion_delegates_valid_recipient() -> None:
    service = _SuggestionServiceStub()

    assert await _suggest_transfer(service) == "save this"

    service.check_and_suggest_beneficiary.assert_awaited_once()
    kwargs = service.check_and_suggest_beneficiary.await_args.kwargs
    assert kwargs["beneficiary_type"] == "transfer"
    assert kwargs["recipient_data"]["account_number"] == "2010000001"


@pytest.mark.asyncio
async def test_mobile_suggestion_skips_self_and_incomplete_target() -> None:
    service = _SuggestionServiceStub()

    assert await _suggest_mobile(service, recipient_phone="08162511023") is None
    assert await _suggest_mobile(service, recipient_phone="") is None
    assert await _suggest_mobile(service, network="") is None

    service.check_and_suggest_beneficiary.assert_not_awaited()


@pytest.mark.asyncio
async def test_mobile_suggestion_delegates_valid_airtime_or_data_target() -> None:
    service = _SuggestionServiceStub()

    assert await _suggest_mobile(service, beneficiary_type="data") == "save this"

    service.check_and_suggest_beneficiary.assert_awaited_once()
    kwargs = service.check_and_suggest_beneficiary.await_args.kwargs
    assert kwargs["beneficiary_type"] == "data"
    assert kwargs["recipient_data"] == {"phone": "08031234567", "network": "MTN", "name": "Tolu"}
