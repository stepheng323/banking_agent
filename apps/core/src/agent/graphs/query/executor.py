"""Query executor - thin dispatch layer for normalized queries."""

from apps.core.src.agent.graphs.__shared__.account_selection.service import find_account_by_bank_name
from apps.core.src.agent.graphs.query.handlers import HANDLER_REGISTRY
from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResult
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    """
    Dispatcher for query execution.

    Each intent maps to exactly one handler.
    All handlers are idempotent and read-only.
    """

    def __init__(self, provider: BankingDataProvider):
        self.provider = provider

    async def execute(
        self,
        query: NormalizedQuery,
        account_id: str,
        account_ids: list[str] | None = None,
        accounts_info: list[dict] | None = None,
        current_page: int = 0,
        page_size: int = 5,
        user_id: str | None = None,
    ) -> QueryResult:
        """
        Execute a normalized query.

        Args:
            query: The normalized query to execute
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
                    summary_text=f"I couldn't find an account matching '{query.account_name}'.",
                )
        elif query.accounts_scope == "single":
            all_account_ids = [account_id]

        handler = HANDLER_REGISTRY.get(query.intent)
        if not handler:
            logger.error("unknown_query_intent", intent=query.intent)
            return QueryResult(summary_text="I couldn't understand that query.")

        try:
            result = await handler(
                self.provider,
                query,
                account_id,
                all_account_ids,
                accounts_info,
                current_page,
                page_size,
                user_id=user_id,  # Explicitly passing it
            )
            result.query_snapshot = query
            return result
        except Exception as e:
            logger.error("query_execution_error", intent=query.intent, error=str(e))
            return QueryResult(summary_text="Something went wrong. Please try again.")

    def _resolve_account_by_name(self, name: str, accounts: list[dict]) -> str | None:
        """Resolve account name to account ID using existing AccountSelectionService."""
        matched = find_account_by_bank_name(accounts, name)
        if matched:
            return matched.get("account_id") or matched.get("mono_account_id")
        return None
