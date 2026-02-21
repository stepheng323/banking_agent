"""Affordability query handler."""

from typing import Any

from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult
from shared.clients.abstractions.banking import BankingDataProvider
from shared.i18n import render_message


async def handle_affordability(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    **kwargs: Any,
) -> QueryResult:
    """Handle affordability queries."""
    language = kwargs.get("language", "en")

    # Get balance
    balance = await provider.get_balance(account_id, real_time=True)
    if not balance:
        return QueryResult(summary_text=render_message("query.affordability.balance_unavailable", language))

    amount = query.amount_check or 0
    can_afford = balance.available_balance >= amount
    remaining = balance.available_balance - amount

    if can_afford:
        msg = render_message(
            "query.affordability.can_afford",
            language,
            {
                "balance": f"{balance.available_balance:,.2f}",
                "amount": f"{amount:,.0f}",
                "remaining": f"{remaining:,.0f}",
            },
        )
        return QueryResult(summary_text=msg)

    shortfall = amount - balance.available_balance
    msg = render_message(
        "query.affordability.cannot_afford",
        language,
        {
            "amount": f"{amount:,.0f}",
            "balance": f"{balance.available_balance:,.2f}",
            "shortfall": f"{shortfall:,.0f}",
        },
    )
    return QueryResult(summary_text=msg)
