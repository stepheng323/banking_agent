from unittest.mock import AsyncMock

import pytest

from banking.transactions.runtime.executors.payout import PayoutExecutor


class _PayoutProvider:
    provider_name = "flutterwave"

    def __init__(self, transfer_result: dict | None = None):
        self.initiate_transfer = AsyncMock(return_value=transfer_result or {"success": True, "status": "success"})


class _ResolverProvider:
    def __init__(self, resolve_result):
        self.resolve_account = AsyncMock(return_value=resolve_result)


@pytest.mark.asyncio
async def test_payout_executor_fails_fast_when_resolution_fails() -> None:
    payout_provider = _PayoutProvider()
    resolver_provider = _ResolverProvider(
        resolve_result=type(
            "ResolutionResult",
            (),
            {"success": False, "error": "invalid account", "account": None},
        )()
    )
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout(
        {
            "amount": 5000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
        }
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    payout_provider.initiate_transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_payout_executor_resolves_then_transfers() -> None:
    payout_provider = _PayoutProvider(
        transfer_result={"success": True, "status": "success", "transaction_id": "tx-123"},
    )
    resolver_provider = _ResolverProvider(
        resolve_result=type(
            "ResolutionResult",
            (),
            {
                "success": True,
                "error": None,
                "account": type(
                    "ResolvedAccount",
                    (),
                    {"account_name": "Tolu A", "account_number": "8162511023", "bank_code": "033"},
                )(),
            },
        )()
    )
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout(
        {
            "amount": 5000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
            "narration": "Test",
        }
    )

    resolver_provider.resolve_account.assert_awaited_once_with("8162511023", "033")
    payout_provider.initiate_transfer.assert_awaited_once_with(
        amount=5000.0,
        recipient_account_number="8162511023",
        recipient_bank_code="033",
        narration="Test",
    )
    assert result["success"] is True
    assert result["resolved_account_name"] == "Tolu A"


@pytest.mark.asyncio
async def test_payout_executor_passes_idempotency_key_as_provider_reference() -> None:
    payout_provider = _PayoutProvider(
        transfer_result={"success": False, "status": "pending", "transaction_id": "tx-123"},
    )
    resolver_provider = _ResolverProvider(
        resolve_result=type(
            "ResolutionResult",
            (),
            {
                "success": True,
                "error": None,
                "account": type(
                    "ResolvedAccount",
                    (),
                    {"account_name": "Tolu A", "account_number": "8162511023", "bank_code": "033"},
                )(),
            },
        )()
    )
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout(
        {
            "amount": 5000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
            "idempotency_key": "idem-123",
        }
    )

    payout_provider.initiate_transfer.assert_awaited_once_with(
        amount=5000.0,
        recipient_account_number="8162511023",
        recipient_bank_code="033",
        narration=None,
        reference="idem-123",
    )
    assert result["success"] is False
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_payout_executor_accepts_matching_provider_metadata() -> None:
    payout_provider = _PayoutProvider()
    resolver_provider = _ResolverProvider(
        resolve_result=type(
            "ResolutionResult",
            (),
            {
                "success": True,
                "error": None,
                "account": type(
                    "ResolvedAccount",
                    (),
                    {"account_name": "Tolu A", "account_number": "8162511023", "bank_code": "033"},
                )(),
            },
        )()
    )
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout(
        {
            "amount": 5000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
            "recipient_bank_code_provider": "flutterwave",
            "recipient_resolution_provider": "flutterwave",
            "payout_provider": "flutterwave",
        }
    )

    assert result["success"] is True
    resolver_provider.resolve_account.assert_awaited_once_with("8162511023", "033")
    payout_provider.initiate_transfer.assert_awaited_once()


@pytest.mark.asyncio
async def test_payout_executor_rejects_explicit_mismatched_provider_metadata() -> None:
    payout_provider = _PayoutProvider()
    resolver_provider = _ResolverProvider(resolve_result=None)
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout(
        {
            "amount": 5000,
            "recipient_account": "8162511023",
            "recipient_bank_code": "033",
            "recipient_bank_code_provider": "mono",
            "payout_provider": "flutterwave",
        }
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Recipient bank code provider mismatch"
    resolver_provider.resolve_account.assert_not_awaited()
    payout_provider.initiate_transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_payout_executor_rejects_invalid_input() -> None:
    payout_provider = _PayoutProvider()
    resolver_provider = _ResolverProvider(resolve_result=None)
    executor = PayoutExecutor(payout_provider=payout_provider, resolver_provider=resolver_provider)

    result = await executor.handle_payout({"amount": 0, "recipient_account": "", "recipient_bank_code": ""})

    assert result["success"] is False
    resolver_provider.resolve_account.assert_not_awaited()
    payout_provider.initiate_transfer.assert_not_awaited()
