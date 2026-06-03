"""Query handler registry."""

from banking.transactions.query.handlers.affordability import handle_affordability
from banking.transactions.query.handlers.analytics import handle_analytics
from banking.transactions.query.handlers.beneficiary import handle_beneficiary_summary
from banking.transactions.query.handlers.time_comparison import handle_time_comparison
from banking.transactions.query.handlers.transactions import (
    handle_transaction_list,
    handle_transaction_search,
)
from banking.transactions.query.models.domain import QueryIntent

HANDLER_REGISTRY = {
    QueryIntent.TRANSACTION_LIST: handle_transaction_list,
    QueryIntent.TRANSACTION_SEARCH: handle_transaction_search,
    QueryIntent.ANALYTICS_SUMMARY: handle_analytics,
    QueryIntent.TIME_COMPARISON: handle_time_comparison,
    QueryIntent.BENEFICIARY_SUMMARY: handle_beneficiary_summary,
    QueryIntent.AFFORDABILITY: handle_affordability,
}
