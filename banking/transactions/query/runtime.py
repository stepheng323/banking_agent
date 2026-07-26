"""Runtime factory for transaction-query workers."""

from typing import Any

from banking.transactions.query.worker import QueryWorker
from shared.clients.abstractions.banking import BankDataProvider


def build_query_worker(
    *,
    llm: Any,
    banking_provider: BankDataProvider,
) -> QueryWorker:
    """Build the query worker through the transaction-query domain boundary."""
    return QueryWorker(llm=llm, banking_provider=banking_provider)
