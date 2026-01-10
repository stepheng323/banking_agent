"""Balance query handler."""

from datetime import date

from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult, QueryResultItem
from shared.clients.abstractions.banking import BankingDataProvider


async def handle_balance(
    provider: BankingDataProvider,
    query: NormalizedQuery,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
) -> QueryResult:
    """Handle balance queries."""
    if query.accounts_scope == "all" and len(account_ids) > 1:
        total = 0.0
        items = []
        for acc_id in account_ids:
            balance = await provider.get_balance(acc_id, real_time=True)
            if balance:
                total += balance.available_balance

                account_info = None
                if accounts_info:
                    account_info = next(
                        (
                            acc
                            for acc in accounts_info
                            if acc.get("account_id") == acc_id or acc.get("mono_account_id") == acc_id
                        ),
                        None,
                    )

                bank_name = account_info.get("bank_name", "Account") if account_info else "Account"
                account_number = account_info.get("account_number", "") if account_info else ""

                items.append(
                    QueryResultItem(
                        id=acc_id[:8],
                        description=bank_name,
                        amount=balance.available_balance,
                        date=query.time_range.end if query.time_range else date.today(),
                        metadata={"bank_name": bank_name, "account_number": account_number, "type": "balance"},
                    )
                )
        return QueryResult(
            summary_text=f"accounts:{len(account_ids)}|total:₦{total:,.2f}",
            items=items,
        )
    else:
        balance = await provider.get_balance(account_id, real_time=True)
        if not balance:
            return QueryResult(summary_text="Could not retrieve balance.")

        account_info = None
        if accounts_info:
            account_info = next(
                (
                    acc
                    for acc in accounts_info
                    if acc.get("account_id") == account_id or acc.get("mono_account_id") == account_id
                ),
                None,
            )

        bank_name = account_info.get("bank_name", "Account") if account_info else "Account"

        return QueryResult(
            summary_text=f"Balance: ₦{balance.available_balance:,.2f}",
            items=[
                QueryResultItem(
                    id=account_id[:8],
                    description=bank_name,
                    amount=balance.available_balance,
                    date=date.today(),
                    metadata={"bank_name": bank_name, "type": "balance"},
                )
            ],
        )
