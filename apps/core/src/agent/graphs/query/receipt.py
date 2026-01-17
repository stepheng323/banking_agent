"""Receipt generation for transactions."""

from apps.core.src.agent.graphs.query.models import QueryResultItem


def format_text_receipt(item: QueryResultItem) -> str:
    """Generate a text-based receipt for a transaction.

    Args:
        item: The transaction item to format

    Returns:
        Formatted text receipt string
    """
    tx_type = item.metadata.get("type", "") if item.metadata else ""
    status_emoji = "✅" if tx_type == "credit" or "success" in str(item.metadata).lower() else "💸"
    type_label = "Credit" if tx_type == "credit" else "Debit"

    bank_name = item.metadata.get("bank_name", "") if item.metadata else ""
    ref_id = item.id or "N/A"

    receipt = f"""*PAYMENT RECEIPT*
━━━━━━━━━━━━━━━━━━━━
*Date:* {item.date.strftime('%B %d, %Y') if item.date else 'Unknown'}
*Amount:* ₦{item.amount:,.2f}
*Type:* {type_label} {status_emoji}
*Description:* {item.description}
*Bank:* {bank_name or 'N/A'}
*Reference:* {ref_id}
━━━━━━━━━━━━━━━━━━━━

_This is an automatically generated receipt._"""

    return receipt
