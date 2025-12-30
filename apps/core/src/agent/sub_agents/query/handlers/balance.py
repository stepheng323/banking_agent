"""Balance query handler."""

from datetime import date

from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_balance(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
) -> QueryResult:
    """Handle balance queries."""
    if query.accounts_scope == "all" and len(account_ids) > 1:
        total = 0.0
        items = []
        for acc_id in account_ids:
            balance = await provider.get_balance(acc_id, real_time=True)
            if balance:
                total += balance.available_balance
                items.append(
                    QueryResultItem(
                        id=acc_id[:8],
                        description="Account",
                        amount=balance.available_balance,
                        date=query.time_range.end if query.time_range else date.today(),
                    )
                )
        return QueryResult(
            summary_text=f"Total balance across {len(account_ids)} accounts: ₦{total:,.2f}",
            items=items,
        )
    else:
        balance = await provider.get_balance(account_id, real_time=True)
        if not balance:
            return QueryResult(summary_text="Could not retrieve balance.")
        return QueryResult(
            summary_text=f"Balance: ₦{balance.available_balance:,.2f}",
            items=[
                QueryResultItem(
                    id=account_id[:8],
                    description="Account",
                    amount=balance.available_balance,
                    date=date.today(),
                )
            ],
        )
