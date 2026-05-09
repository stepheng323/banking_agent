"""Query executor - thin dispatch layer for query execution contracts."""

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, cast

from apps.chat.src.agent.graphs.__shared__.account_selection.service import find_account_by_bank_name
from apps.chat.src.agent.graphs.query.handlers import HANDLER_REGISTRY
from apps.chat.src.agent.graphs.query.models import QueryExecutionContract, QueryIntent, QueryResult
from shared.clients.abstractions.banking import BankDataProvider
from shared.i18n import render_message
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
        query: QueryExecutionContract,
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

        if query.accounts_scope == "single" and query.account_name and accounts_info:
            resolved_id = self._resolve_account_by_name(query.account_name, accounts_info)
            if resolved_id:
                account_id = resolved_id
                all_account_ids = [resolved_id]
            else:
                return QueryResult(
                    summary_text=render_message(
                        "query.account.not_found_by_name",
                        language,
                        {"account_name": query.account_name},
                    ),
                )
        elif query.accounts_scope == "single":
            all_account_ids = [account_id]

        handler = HANDLER_REGISTRY.get(query.intent)
        if not handler:
            logger.error("unknown_query_intent", intent=query.intent)
            return QueryResult(summary_text=render_message("query.error.unknown_intent", language))

        typed_handler = cast(Callable[..., Awaitable[QueryResult]], handler)
        started_at = perf_counter()

        try:
            if query.intent in {QueryIntent.TRANSACTION_LIST, QueryIntent.TRANSACTION_SEARCH}:
                result = await typed_handler(
                    self.provider,
                    query,
                    account_id,
                    all_account_ids,
                    accounts_info,
                    current_page,
                    page_size,
                    user_id=user_id,  # Explicitly passing it
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
                    user_id=user_id,  # Explicitly passing it
                    language=language,
                )
            result.query_contract = query
            logger.info(
                "query_trace",
                turn_id=(trace_context or {}).get("turn_id"),
                inbound_message_id=(trace_context or {}).get("inbound_message_id"),
                query_phase="execution",
                latency_ms=round((perf_counter() - started_at) * 1000.0, 2),
                outcome="ok",
                intent=query.intent.value,
                cache_reused=result.cache_reused,
                continuation_type=continuation_type,
            )
            return result
        except Exception as e:
            logger.error("query_execution_error", intent=query.intent, error=str(e))
            logger.info(
                "query_trace",
                turn_id=(trace_context or {}).get("turn_id"),
                inbound_message_id=(trace_context or {}).get("inbound_message_id"),
                query_phase="execution",
                latency_ms=round((perf_counter() - started_at) * 1000.0, 2),
                outcome="failed",
                intent=query.intent.value,
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
