"""Query handler registry."""

from apps.chat.src.agent.workers.query.handlers.affordability import handle_affordability
from apps.chat.src.agent.workers.query.handlers.analytics import handle_analytics
from apps.chat.src.agent.workers.query.handlers.beneficiary import handle_beneficiary_summary
from apps.chat.src.agent.workers.query.handlers.time_comparison import handle_time_comparison
from apps.chat.src.agent.workers.query.handlers.transactions import (
    handle_transaction_list,
    handle_transaction_search,
)
from apps.chat.src.agent.workers.query.models.domain import QueryIntent

HANDLER_REGISTRY = {
    QueryIntent.TRANSACTION_LIST: handle_transaction_list,
    QueryIntent.TRANSACTION_SEARCH: handle_transaction_search,
    QueryIntent.ANALYTICS_SUMMARY: handle_analytics,
    QueryIntent.TIME_COMPARISON: handle_time_comparison,
    QueryIntent.BENEFICIARY_SUMMARY: handle_beneficiary_summary,
    QueryIntent.AFFORDABILITY: handle_affordability,
}
