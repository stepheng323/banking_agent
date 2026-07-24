"""Transaction detail handler."""

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import (
    QueryRequest,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.services.fetching.fetch import fetch_and_filter, parse_date
from shared.clients.abstractions.banking import BankDataProvider


async def handle_transaction_detail(
    provider: BankDataProvider,
    contract: QueryRequest,
    account_id: str,
    account_ids: list[str],
    accounts_info: list[dict] | None = None,
    current_page: int = 0,
    page_size: int = 5,
    user_id: str | None = None,
    language: str = "en",
) -> QueryResult:
    """Handle transaction detail queries."""
    transactions = await fetch_and_filter(
        provider,
        contract,
        account_id,
        account_ids,
        accounts_info,
        user_id=user_id,
    )

    if not transactions:
        return QueryResult(
            summary_text=render_message("query.format.no_matching_transactions", language),
            items=[],
            has_more=False,
        )

    resolution_policy = contract.resolution_policy or "ask_if_ambiguous"

    if resolution_policy == "strict_single" and len(transactions) > 1:
        # Strictly one requested, but found multiple.
        # Could either return the first or return ambiguity list.
        # We will fallback to ask_if_ambiguous behavior for safety if not handled earlier.
        resolution_policy = "ask_if_ambiguous"

    if resolution_policy == "latest_if_ambiguous" and len(transactions) > 1:
        transactions = [transactions[0]]

    if len(transactions) == 1:
        t = transactions[0]
        # Return exact detail
        fact_field = contract.answer_fact_field

        amount_val = abs(t.get("amount", 0))
        item = QueryResultItem(
            id=t.get("id", "")[:8] if t.get("id") else "0",
            description=t.get("narration", "Transaction"),
            amount=amount_val,
            date=parse_date(t.get("date", "")),
            metadata={
                **t,
                "bank_name": t.get("bank_name", ""),
                "type": t.get("type", ""),
                "counterparty": t.get("counterparty"),
                "status": t.get("status", "unknown"),
                "reference": t.get("reference", ""),
                "category": t.get("category", ""),
            },
        )

        summary_text = "Here is the transaction you asked about."
        if fact_field == "reference":
            ref = t.get("reference") or "No reference available."
            summary_text = f"The reference is {ref}."
        elif fact_field == "status":
            status = t.get("status", "unknown")
            summary_text = f"The status is {status}."
        elif fact_field == "date":
            date_val = t.get("date", "")[:10]
            summary_text = f"It occurred on {date_val}."
        elif fact_field == "amount":
            summary_text = f"The amount was ₦{amount_val:,.0f}."

        # The executor formatter will set surface_view to DIRECT_ANSWER if items=1 and request_shape=FACT
        return QueryResult(
            summary_text=summary_text,
            items=[item],
            has_more=False,
        )

    # Ambiguous - multiple matches found and policy is ask_if_ambiguous
    start_idx = current_page * page_size
    end_idx = start_idx + page_size
    paginated_txns = transactions[start_idx:end_idx]

    items = [
        QueryResultItem(
            id=t.get("id", "")[:8] if t.get("id") else str(i),
            description=t.get("narration", "Transaction"),
            amount=abs(t.get("amount", 0)),
            date=parse_date(t.get("date", "")),
            metadata={
                **t,
                "bank_name": t.get("bank_name", ""),
                "type": t.get("type", ""),
                "counterparty": t.get("counterparty"),
            },
        )
        for i, t in enumerate(paginated_txns)
    ]

    has_more = len(transactions) > end_idx
    summary_text = f"I found {len(transactions)} matching transactions. Which one did you mean?"

    return QueryResult(
        summary_text=summary_text,
        items=items,
        has_more=has_more,
    )
