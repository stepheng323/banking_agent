import pytest

from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.graphs.transfer.nodes.selection import select_source_account
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


@pytest.mark.asyncio
async def test_transfer_source_bank_pending_account_requires_revision() -> None:
    ctx = TransferContext(
        phone_number="2348162511023",
        language="en",
        accounts=[
            {
                "id": "ready-1",
                "bank_name": "Zenith Bank",
                "account_name": "Main Account",
                "account_number": "00009384",
                "mandate_status": "ready",
                "is_default": True,
            }
        ],
        all_accounts=[
            {
                "id": "pending-1",
                "bank_name": "First Bank",
                "account_name": "First Account",
                "account_number": "0334555167",
                "mandate_status": "pending",
                "extra_data": {
                    "transfer_destinations": [{"bank_name": "NIBSS Bank", "account_number": "0001112223"}]
                },
            },
            {
                "id": "ready-1",
                "bank_name": "Zenith Bank",
                "account_name": "Main Account",
                "account_number": "00009384",
                "mandate_status": "ready",
                "is_default": True,
            },
        ],
    )
    payload = TransferPayload(amount=10000, recipient_name="Tolu", source_bank_name="First Bank")

    result = await select_source_account(payload, ctx)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.update_message is not None
    assert "First Bank account is linked" in result.update_message
    assert "not ready for payments yet" in result.update_message
    assert result.prompt is not None
    assert "Zenith Bank" in result.prompt
