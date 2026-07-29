"""Query executor - thin dispatch layer for typed query operations."""

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, cast

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.handlers.registry import handler_for_request
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.models.operations import (
    AccountById,
    NamedAccount,
    QueryRequest,
    RetrieveOperation,
    SelectedAccounts,
)
from banking.transactions.shared.account_selection.service import find_account_by_bank_name
from shared.clients.abstractions.banking import BankDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    """
    Dispatcher for query execution.

    Each intent maps to exactly one handler.
    All handlers are idempotent and read-only.
    """

    def __init__(self, provider: BankDataProvider):
        self.provider = provider

    async def execute(
        self,
        query: QueryRequest,
        account_id: str,
        account_ids: list[str] | None = None,
        accounts_info: list[dict] | None = None,
        current_page: int = 0,
        page_size: int = 5,
        user_id: str | None = None,
        language: str = "en",
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
        session_cache: dict[str, object] | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> QueryResult:
        """
        Execute a query execution contract.

        Args:
            query: The query contract to execute
            account_id: Primary account ID
            account_ids: All account IDs for multi-account queries
            accounts_info: Account details for name resolution
            current_page: Pagination page (0-indexed)
            page_size: Number of items per page
            user_id: ID of the user for local DB lookups

        Returns:
            QueryResult with context_key for follow-ups
        """
        all_account_ids = account_ids or [account_id]

        accounts = query.accounts
        if isinstance(accounts, NamedAccount) and accounts_info:
            resolved_id = self._resolve_account_by_name(accounts.name, accounts_info)
            if resolved_id:
                account_id = resolved_id
                all_account_ids = [resolved_id]
            else:
                return QueryResult(
                    summary_text=render_message(
                        "query.account.not_found_by_name",
                        language,
                        {"account_name": accounts.name},
                    ),
                )
        elif isinstance(accounts, NamedAccount):
            all_account_ids = [account_id]
        elif isinstance(accounts, AccountById):
            account_id = accounts.account_id
            all_account_ids = [accounts.account_id]
        elif isinstance(accounts, SelectedAccounts):
            all_account_ids = list(dict.fromkeys(accounts.account_ids))
            account_id = all_account_ids[0]

        handler = handler_for_request(query)
        if not handler:
            logger.error("unknown_query_operation", operation=query.operation.kind)
            return QueryResult(summary_text=render_message("query.error.unknown_intent", language))

        typed_handler = cast(Callable[..., Awaitable[QueryResult]], handler)
        started_at = perf_counter()

        try:
            is_transaction_list = (
                isinstance(query.operation, RetrieveOperation) and query.operation.projection.shape == "list"
            )
            if is_transaction_list:
                result = await typed_handler(
                    self.provider,
                    query,
                    account_id,
                    all_account_ids,
                    accounts_info,
                    current_page,
                    page_size,
                    user_id=user_id,
                    language=language,
                    continuation_type=continuation_type,
                    continuation_delta_type=continuation_delta_type,
                    session_cache=session_cache,
                    trace_context=trace_context,
                )
            else:
                result = await typed_handler(
                    self.provider,
                    query,
                    account_id,
                    all_account_ids,
                    accounts_info,
                    current_page,
                    page_size,
                    user_id=user_id,
                    language=language,
                )
            result.query_request = query
            logger.info(
                "query_trace",
                turn_id=(trace_context or {}).get("turn_id"),
                inbound_message_id=(trace_context or {}).get("inbound_message_id"),
                query_phase="execution",
                latency_ms=round((perf_counter() - started_at) * 1000.0, 2),
                outcome="ok",
                operation=query.operation.kind,
                cache_reused=result.cache_reused,
                continuation_type=continuation_type,
            )
            return result
        except Exception as e:
            logger.error("query_execution_error", operation=query.operation.kind, error=str(e))
            logger.info(
                "query_trace",
                turn_id=(trace_context or {}).get("turn_id"),
                inbound_message_id=(trace_context or {}).get("inbound_message_id"),
                query_phase="execution",
                latency_ms=round((perf_counter() - started_at) * 1000.0, 2),
                outcome="failed",
                operation=query.operation.kind,
                cache_reused=False,
                continuation_type=continuation_type,
            )
            return QueryResult(summary_text=render_message("query.error.execution_failed", language))

    def _resolve_account_by_name(self, name: str, accounts: list[dict]) -> str | None:
        """Resolve account name to account ID using existing AccountSelectionService."""
        matched = find_account_by_bank_name(accounts, name)
        if matched:
            return matched.get("account_id") or matched.get("mono_account_id")
        return None
