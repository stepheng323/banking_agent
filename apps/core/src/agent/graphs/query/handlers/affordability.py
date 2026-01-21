"""Affordability query handler."""

from typing import Any

from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_affordability(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    **kwargs: Any,
) -> QueryResult:
    """Handle affordability queries."""
    # Get balance
    balance = await provider.get_balance(account_id, real_time=True)
    if not balance:
        return QueryResult(summary_text="Could not retrieve balance.")

    amount = query.amount_check or 0
    can_afford = balance.available_balance >= amount
    remaining = balance.available_balance - amount

    if can_afford:
        msg = f"Your balance (₦{balance.available_balance:,.2f}) covers ₦{amount:,.0f}. Remaining: ₦{remaining:,.0f}"
        return QueryResult(summary_text=msg)
    else:
        shortfall = amount - balance.available_balance
        msg = f"₦{amount:,.0f} exceeds your balance (₦{balance.available_balance:,.2f}). Shortfall: ₦{shortfall:,.0f}"
        return QueryResult(summary_text=msg)
