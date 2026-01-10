"""Query handlers registry and base types."""

from apps.core.src.agent.graphs.query.handlers.affordability import handle_affordability
from apps.core.src.agent.graphs.query.handlers.analytics import handle_analytics
from apps.core.src.agent.graphs.query.handlers.balance import handle_balance
from apps.core.src.agent.graphs.query.handlers.beneficiary import handle_beneficiary_summary
from apps.core.src.agent.graphs.query.handlers.time_comparison import handle_time_comparison
from apps.core.src.agent.graphs.query.handlers.transactions import (
    handle_transaction_list,
    handle_transaction_search,
)
from apps.core.src.agent.graphs.query.models import QueryIntent

HANDLER_REGISTRY = {
    QueryIntent.BALANCE_QUERY: handle_balance,
    QueryIntent.TRANSACTION_LIST: handle_transaction_list,
    QueryIntent.TRANSACTION_SEARCH: handle_transaction_search,
    QueryIntent.ANALYTICS_SUMMARY: handle_analytics,
    QueryIntent.TIME_COMPARISON: handle_time_comparison,
    QueryIntent.BENEFICIARY_SUMMARY: handle_beneficiary_summary,
    QueryIntent.AFFORDABILITY: handle_affordability,
}

__all__ = [
    "HANDLER_REGISTRY",
    "handle_balance",
    "handle_transaction_list",
    "handle_transaction_search",
    "handle_analytics",
    "handle_time_comparison",
    "handle_beneficiary_summary",
    "handle_affordability",
]
