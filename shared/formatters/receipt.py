"""Receipt formatter for transaction receipts."""

from datetime import datetime
from typing import Any

from shared.database.models import Transaction


def format_transaction_receipt(transaction: Transaction) -> str:
    """
    Format a transaction receipt as a text message.

    Args:
        transaction: Transaction database model instance

    Returns:
        Formatted receipt string
    """
    # Format date and time
    created_at = transaction.created_at
    if isinstance(created_at, str):
        # Parse if string
        try:
            created_at = datetime.fromisoformat(
                created_at.replace("Z", "+00:00"))
        except Exception:
            created_at = datetime.utcnow()

    date_str = created_at.strftime("%d %b %Y") if created_at else "N/A"
    time_str = created_at.strftime("%I:%M %p") if created_at else "N/A"

    # Format amount
    amount = transaction.amount
    currency = transaction.currency or "NGN"
    amount_str = f"₦{amount:,.2f}" if currency == "NGN" else f"{currency} {amount:,.2f}"

    # Transaction ID
    txn_id = transaction.transaction_id or "Pending"

    # Recipient details
    recipient_name = transaction.recipient_name or "N/A"
    recipient_account = transaction.recipient_account_number
    recipient_bank = transaction.recipient_bank_name or transaction.recipient_bank_code or "N/A"

    # Source details
    source_account = transaction.source_account_number or "N/A"
    source_bank = transaction.source_bank_name or "N/A"

    # Narration
    narration = transaction.narration or "No narration"

    # Status
    status = transaction.status.upper()

    receipt = f"""📄 TRANSACTION RECEIPT

Transaction ID: {txn_id}
Date: {date_str}
Time: {time_str}
Status: {status}

Amount: {amount_str}
Currency: {currency}

From:
{source_account} ({source_bank})

To:
{recipient_name}
{recipient_account} ({recipient_bank})

Narration: {narration}

Thank you for using our service! 💙"""

    return receipt
