"""Source account selection logic."""

from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.orchestrator.models.domain import TransferOutcome, TransferResult


async def select_source_account(
    payload: TransferPayload,
    ctx: TransferContext,
) -> TransferResult:
    """Select source account if not provided."""
    if payload.source_account_id:
        return TransferResult(outcome=TransferOutcome.OK)

    accounts = ctx.accounts
    if not accounts:
        return TransferResult(outcome=TransferOutcome.FAILED, error="No accounts available.")

    # Auto-select if only one
    if len(accounts) == 1:
        acc = accounts[0]
        return TransferResult(
            outcome=TransferOutcome.OK,
            patch={
                "source_account_id": str(acc.get("id")),
                "source_bank_name": acc.get("bank_name"),
                "source_account_number": acc.get("account_number"),
            },
        )

    # Check default
    default = next((a for a in accounts if a.get("is_default")), None)
    if default:
        return TransferResult(
            outcome=TransferOutcome.OK,
            patch={
                "source_account_id": str(default.get("id")),
                "source_bank_name": default.get("bank_name"),
                "source_account_number": default.get("account_number"),
            },
        )

    # Try resolving via source_bank_name (e.g. "from my Access")
    if payload.source_bank_name and not payload.source_account_id:
        target_bank = payload.source_bank_name.lower()
        candidates = [
            a for a in accounts
            if target_bank in (a.get("bank_name") or "").lower()
            or (a.get("alias") and target_bank in a.get("alias").lower())
        ]
        if candidates:
            acc = candidates[0]
            return TransferResult(
                outcome=TransferOutcome.OK,
                patch={
                    "source_account_id": str(acc.get("id")),
                    "source_bank_name": acc.get("bank_name"),
                    "source_account_number": acc.get("account_number"),
                },
            )

    # Smart Selection: If 2 accounts and 1 is the recipient, use the other.
    if len(accounts) == 2 and payload.recipient_account:
        recipient_acc_num = payload.recipient_account
        
        # Check if recipient is one of ours
        matching_recipient = next((a for a in accounts if a.get("account_number") == recipient_acc_num), None)
        
        if matching_recipient:
            # Recipient is one of ours. Select the other one.
            source_acc = next((a for a in accounts if a.get("account_number") != recipient_acc_num), None)
            if source_acc:
                return TransferResult(
                    outcome=TransferOutcome.OK,
                    patch={
                        "source_account_id": str(source_acc.get("id")),
                        "source_bank_name": source_acc.get("bank_name"),
                        "source_account_number": source_acc.get("account_number"),
                    },
                )

    return TransferResult(
        outcome=TransferOutcome.NEEDS_INPUT,
        required_fields=["source_account_id"],
        prompt="Which account should I debit?",
    )
