from datetime import timedelta

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.transfer.models.types import TransferContext, TransferPayload
from apps.chat.src.agent.workers.transfer.nodes.selection import select_source_account
from banking.accounts.mandate_state import mandate_authorization_metadata
from shared.utils.datetime import utc_now_naive


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
                "extra_data": {"transfer_destinations": [{"bank_name": "NIBSS Bank", "account_number": "0001112223"}]},
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


@pytest.mark.asyncio
async def test_transfer_source_bank_expired_pending_account_requires_reinitiation() -> None:
    expired_metadata = mandate_authorization_metadata(
        {},
        created_at=utc_now_naive() - timedelta(hours=2),
        transfer_destinations=[{"bank_name": "NIBSS Bank", "account_number": "0001112223"}],
    )
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
                "extra_data": expired_metadata,
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
    assert "authorization has expired" in result.update_message
    assert "NIBSS Bank" not in result.update_message
    assert result.prompt is not None
    assert "Zenith Bank" in result.prompt
