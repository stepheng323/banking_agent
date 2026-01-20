"""Confirmation logic."""

from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult
from shared.formatters.transfer import format_transfer_summary


def build_confirmation(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransferResult:
    """Build confirmation summary."""
    snap = {
        "amount": payload.amount,
        "recipient_name": payload.recipient_resolved_name or payload.recipient_name,
        "recipient_bank": payload.recipient_bank_name,
        "recipient_account": payload.recipient_account,
        "source_bank": payload.source_bank_name,
        "source_account": payload.source_account_number,
        "narration": payload.narration,
    }
    summary = format_transfer_summary(
        {
            "amount": payload.amount,
            "recipientName": payload.recipient_resolved_name or payload.recipient_name,
            "recipientBank": payload.recipient_bank_name,
            "recipientAccount": payload.recipient_account,
            "sourceBank": payload.source_bank_name,
            "sourceAccount": payload.source_account_number,
            "narration": payload.narration,
        }
    )

    return TransferResult(
        outcome=TransferOutcome.NEEDS_CONFIRMATION,
        confirmation_snapshot=snap,
        confirmation_summary=summary,
    )
