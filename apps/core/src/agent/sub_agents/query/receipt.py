"""Receipt generation for transactions."""

from apps.core.src.agent.sub_agents.query.models import QueryResultItem


def format_text_receipt(item: QueryResultItem) -> str:
    """Generate a text-based receipt for a transaction.

    Args:
        item: The transaction item to format

    Returns:
        Formatted text receipt string
    """
    status_emoji = "✅" if item.type == "credit" or "success" in str(item).lower() else "💸"
    type_label = "Credit" if item.type == "credit" else "Debit"

    receipt = f"""📄 *PAYMENT RECEIPT*
━━━━━━━━━━━━━━━━━━━━
*Date:* {item.date}
*Amount:* ₦{item.amount:,.2f}
*Type:* {type_label} {status_emoji}
*Description:* {item.description}
━━━━━━━━━━━━━━━━━━━━

_This is an automatically generated receipt._"""

    return receipt
