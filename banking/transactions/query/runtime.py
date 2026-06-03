"""Runtime factory for transaction-query workers."""

from typing import Any

import redis.asyncio as redis

from banking.transactions.query.session import QuerySessionManager
from banking.transactions.query.worker import QueryWorker
from shared.clients.abstractions.banking import BankDataProvider


def build_query_session_manager(redis_client: redis.Redis) -> QuerySessionManager:
    """Build the query session manager through the transaction-query boundary."""
    return QuerySessionManager(redis_client)


def build_query_worker(
    *,
    llm: Any,
    banking_provider: BankDataProvider,
    redis_client: redis.Redis,
) -> QueryWorker:
    """Build the query worker through the transaction-query domain boundary."""
    return QueryWorker(
        llm=llm,
        banking_provider=banking_provider,
        session_manager=build_query_session_manager(redis_client),
    )
