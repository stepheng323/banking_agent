"""Query executor - dispatches normalized queries to handlers."""

from typing import Any

from apps.core.src.agent.sub_agents.query.models import (
    Filters,
    NormalizedQuery,
    QueryIntent,
    QueryResult,
    QueryResultItem,
    match_category,
)
from apps.core.src.agent.tools.account_selection.service import AccountSelectionService
from shared.clients.providers.mono import MonoClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryExecutor:
    """
    Dispatcher for query execution.

    Each intent maps to exactly one handler.
    All handlers are idempotent and read-only.
    """

    def __init__(self, mono_client: MonoClient):
        self.mono = mono_client

    async def execute(
        self,
        query: NormalizedQuery,
        account_id: str,
        account_ids: list[str] | None = None,
        accounts_info: list[dict] | None = None,
    ) -> QueryResult:
        """
        Execute a normalized query.

        Args:
            query: The normalized query to execute
            account_id: Primary account ID
            account_ids: All account IDs for multi-account queries
            accounts_info: Account details for name resolution

        Returns:
            QueryResult with context_key for follow-ups
        """
        all_account_ids = account_ids or [account_id]

        # Resolve account scope
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

        handlers = {
            QueryIntent.BALANCE_QUERY: self._handle_balance,
            QueryIntent.TRANSACTION_LIST: self._handle_transaction_list,
            QueryIntent.TRANSACTION_SEARCH: self._handle_transaction_search,
            QueryIntent.ANALYTICS_SUMMARY: self._handle_analytics,
            QueryIntent.TIME_COMPARISON: self._handle_time_comparison,
            QueryIntent.BENEFICIARY_SUMMARY: self._handle_beneficiary_summary,
            QueryIntent.AFFORDABILITY: self._handle_affordability,
        }

        handler = handlers.get(query.intent)
        if not handler:
            logger.error("unknown_query_intent", intent=query.intent)
            return QueryResult(summary_text="I couldn't understand that query.")

        try:
            result = await handler(query, account_id, all_account_ids)
            result.query_snapshot = query
            return result
        except Exception as e:
            logger.error("query_execution_error", intent=query.intent, error=str(e))
            return QueryResult(summary_text="Something went wrong. Please try again.")

    def _resolve_account_by_name(self, name: str, accounts: list[dict]) -> str | None:
        """Resolve account name to account ID using existing AccountSelectionService."""

        matched = AccountSelectionService.find_account_by_bank_name(accounts, name)
        if matched:
            return matched.get("account_id") or matched.get("mono_account_id")
        return None

    async def _handle_balance(self, query: NormalizedQuery, account_id: str, account_ids: list[str]) -> QueryResult:
        """Handle balance queries."""
        if query.accounts_scope == "all" and len(account_ids) > 1:
            total = 0.0
            items = []
            for acc_id in account_ids:
                balance = await self.mono.get_balance(acc_id, real_time=True)
                total += balance.balance_naira
                items.append(
                    QueryResultItem(
                        id=acc_id[:8],
                        description=balance.account_name or "Account",
                        amount=balance.balance_naira,
                        date=query.time_range.end if query.time_range else __import__("datetime").date.today(),
                    )
                )
            return QueryResult(
                summary_text=f"Total balance across {len(account_ids)} accounts: ₦{total:,.2f}",
                items=items,
            )
        else:
            balance = await self.mono.get_balance(account_id, real_time=True)
            return QueryResult(
                summary_text=f"Balance: ₦{balance.balance_naira:,.2f}",
                items=[
                    QueryResultItem(
                        id=account_id[:8],
                        description=balance.account_name or "Account",
                        amount=balance.balance_naira,
                        date=__import__("datetime").date.today(),
                    )
                ],
            )

    async def _handle_transaction_list(
        self, query: NormalizedQuery, account_id: str, account_ids: list[str]
    ) -> QueryResult:
        """Handle transaction list queries."""
        transactions = await self._fetch_and_filter(query, account_id, account_ids)
        limit = query.aggregation.limit if query.aggregation else 10

        items = [
            QueryResultItem(
                id=t.get("id", "")[:8] if t.get("id") else str(i),
                description=t.get("narration", "Transaction"),
                amount=t.get("amount", 0) / 100,
                date=self._parse_date(t.get("date", "")),
                metadata={"type": t.get("type")},
            )
            for i, t in enumerate(transactions[:limit])
        ]

        return QueryResult(
            summary_text=f"Found {len(transactions)} transactions",
            items=items,
            has_more=len(transactions) > limit,
        )

    async def _handle_transaction_search(
        self, query: NormalizedQuery, account_id: str, account_ids: list[str]
    ) -> QueryResult:
        """Handle transaction search (same as list but with merchant filter)."""
        return await self._handle_transaction_list(query, account_id, account_ids)

    async def _handle_analytics(self, query: NormalizedQuery, account_id: str, account_ids: list[str]) -> QueryResult:
        """Handle analytics summary queries."""
        transactions = await self._fetch_and_filter(query, account_id, account_ids)

        if not query.aggregation:
            return QueryResult(summary_text="No aggregation specified.")

        agg_type = query.aggregation.type

        if agg_type == "sum":
            total = sum(t.get("amount", 0) for t in transactions) / 100
            return QueryResult(summary_text=f"Total: ₦{total:,.2f}")

        elif agg_type == "average":
            if transactions:
                avg = sum(t.get("amount", 0) for t in transactions) / len(transactions) / 100
                return QueryResult(summary_text=f"Average: ₦{avg:,.2f}")
            return QueryResult(summary_text="No transactions found.")

        elif agg_type == "count":
            return QueryResult(summary_text=f"Count: {len(transactions)} transactions")

        elif agg_type == "largest":
            limit = query.aggregation.limit or 5
            sorted_txns = sorted(transactions, key=lambda t: t.get("amount", 0), reverse=True)
            items = [
                QueryResultItem(
                    id=t.get("id", "")[:8] if t.get("id") else str(i),
                    description=t.get("narration", "Transaction"),
                    amount=t.get("amount", 0) / 100,
                    date=self._parse_date(t.get("date", "")),
                )
                for i, t in enumerate(sorted_txns[:limit])
            ]
            return QueryResult(
                summary_text=f"Top {limit} largest transactions",
                items=items,
            )

        elif agg_type == "breakdown":
            return await self._aggregate_breakdown(transactions, query)

        return QueryResult(summary_text="Aggregation completed.")

    async def _handle_time_comparison(
        self, query: NormalizedQuery, account_id: str, account_ids: list[str]
    ) -> QueryResult:
        """Handle time comparison queries (vs last month, etc.)."""
        # TODO: Implement period-over-period comparison
        return QueryResult(summary_text="Time comparison coming soon.")

    async def _handle_beneficiary_summary(
        self, query: NormalizedQuery, account_id: str, account_ids: list[str]
    ) -> QueryResult:
        """Handle beneficiary summary queries."""
        transactions = await self._fetch_and_filter(query, account_id, account_ids)

        # Filter to debits (outgoing)
        debits = [t for t in transactions if t.get("type") == "debit"]

        # Group by counterparty
        from collections import defaultdict

        counterparties: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "count": 0})
        for t in debits:
            name = self._extract_counterparty(t.get("narration", ""))
            counterparties[name]["total"] += t.get("amount", 0)
            counterparties[name]["count"] += 1

        sorted_cp = sorted(counterparties.items(), key=lambda x: x[1]["total"], reverse=True)
        limit = query.aggregation.limit if query.aggregation else 5

        items = [
            QueryResultItem(
                id=str(i),
                description=name,
                amount=data["total"] / 100,
                date=query.time_range.end if query.time_range else __import__("datetime").date.today(),
                metadata={"count": data["count"]},
            )
            for i, (name, data) in enumerate(sorted_cp[:limit])
        ]

        return QueryResult(
            summary_text=f"Top {len(items)} recipients",
            items=items,
        )

    async def _handle_affordability(
        self, query: NormalizedQuery, account_id: str, account_ids: list[str]
    ) -> QueryResult:
        """Handle affordability queries."""
        # Get balance
        balance = await self.mono.get_balance(account_id, real_time=True)
        amount = query.amount_check or 0

        can_afford = balance.balance_naira >= amount
        remaining = balance.balance_naira - amount

        if can_afford:
            msg = f"Your balance (₦{balance.balance_naira:,.2f}) covers ₦{amount:,.0f}. Remaining: ₦{remaining:,.0f}"
            return QueryResult(summary_text=msg)
        else:
            shortfall = amount - balance.balance_naira
            msg = f"₦{amount:,.0f} exceeds your balance (₦{balance.balance_naira:,.2f}). Shortfall: ₦{shortfall:,.0f}"
            return QueryResult(summary_text=msg)

    async def _fetch_and_filter(
        self,
        query: NormalizedQuery,
        account_id: str,
        account_ids: list[str],
    ) -> list[dict]:
        """Fetch transactions and apply filters."""
        # Determine date range
        start = query.time_range.start.isoformat() if query.time_range else None
        end = query.time_range.end.isoformat() if query.time_range else None

        # Fetch transactions
        if query.accounts_scope == "all" and len(account_ids) > 1:
            all_txns = []
            for acc_id in account_ids:
                txns = await self.mono.get_transactions(acc_id, start=start, end=end, limit=100)
                all_txns.extend([t.model_dump() if hasattr(t, "model_dump") else t for t in txns])
            transactions = sorted(all_txns, key=lambda t: t.get("date", ""), reverse=True)
        else:
            txns = await self.mono.get_transactions(account_id, start=start, end=end, limit=100)
            transactions = [t.model_dump() if hasattr(t, "model_dump") else t for t in txns]

        # Apply filters
        if query.filters:
            transactions = self._apply_filters(transactions, query.filters)

        return transactions

    def _apply_filters(self, transactions: list[dict], filters: Filters) -> list[dict]:
        """Apply filters to transaction list."""
        result = transactions

        if filters.min_amount is not None:
            result = [t for t in result if t.get("amount", 0) >= filters.min_amount * 100]

        if filters.max_amount is not None:
            result = [t for t in result if t.get("amount", 0) <= filters.max_amount * 100]

        if filters.transaction_type:
            result = [t for t in result if t.get("type") == filters.transaction_type]

        if filters.category:
            result = [t for t in result if match_category(t.get("narration", ""), filters.category)]

        if filters.merchant:
            result = [t for t in result if any(m.lower() in t.get("narration", "").lower() for m in filters.merchant)]

        if filters.exclude:
            result = [
                t for t in result if not any(e.lower() in t.get("narration", "").lower() for e in filters.exclude)
            ]

        return result

    async def _aggregate_breakdown(self, transactions: list[dict], query: NormalizedQuery) -> QueryResult:
        """Aggregate transactions by day/category/merchant."""
        from collections import defaultdict

        group_by = query.aggregation.group_by if query.aggregation else "day"
        grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"debit": 0, "credit": 0, "count": 0})

        for t in transactions:
            if group_by == "day":
                key = t.get("date", "")[:10]
            elif group_by == "category":
                from apps.core.src.agent.sub_agents.query.models import detect_category

                key = detect_category(t.get("narration", "")) or "other"
            elif group_by == "merchant":
                key = self._extract_counterparty(t.get("narration", ""))
            else:
                key = t.get("date", "")[:10]

            tx_type = t.get("type", "unknown")
            if tx_type in ("debit", "credit"):
                grouped[key][tx_type] += t.get("amount", 0)
                grouped[key]["count"] += 1

        items = [
            QueryResultItem(
                id=str(i),
                description=key,
                amount=(data["debit"] + data["credit"]) / 100,
                date=self._parse_date(key) if group_by == "day" else __import__("datetime").date.today(),
                metadata={"debit": data["debit"] / 100, "credit": data["credit"] / 100, "count": data["count"]},
            )
            for i, (key, data) in enumerate(sorted(grouped.items(), reverse=True)[:10])
        ]

        return QueryResult(
            summary_text=f"Breakdown by {group_by}",
            items=items,
        )

    def _parse_date(self, date_str: str) -> __import__("datetime").date:
        """Parse date string to date object."""
        from datetime import date, datetime

        if not date_str:
            return date.today()
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            return date.today()

    def _extract_counterparty(self, narration: str) -> str:
        """Extract counterparty name from narration."""
        if not narration:
            return "Unknown"

        narration = narration.strip()
        prefixes = ["Transfer to ", "Transfer from ", "Payment to ", "From ", "To "]
        for prefix in prefixes:
            if narration.startswith(prefix):
                narration = narration[len(prefix) :]
                break

        if len(narration) > 25:
            narration = narration[:22] + "..."

        return narration
