from typing import Any

import pytest

from apps.chat.src.agent.graphs.transfer.validators.account_validator import AccountValidator


class _ValidationServiceStub:
    async def validate_account_and_balance(
        self,
        *,
        account_number: str,
        bank_code: str,
        source_account_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert account_number == "1234567890"
        assert bank_code == "044"
        assert source_account_id == "source-1"
        return (
            {"success": True, "account_name": "Mum"},
            {"available_balance": 50000},
        )


@pytest.mark.asyncio
async def test_account_validator_does_not_send_manual_progress_message() -> None:
    validator = AccountValidator(
        validation_service=_ValidationServiceStub(),  # type: ignore[arg-type]
        publisher=object(),  # type: ignore[arg-type]
    )

    resolved, balance = await validator.validate(
        account_number="1234567890",
        bank_code="044",
        source_account_id="source-1",
        phone_number="2348000000000",
    )

    assert resolved["success"] is True
    assert balance["available_balance"] == 50000
