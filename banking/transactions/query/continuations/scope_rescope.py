"""Scope-broadening recovery for active query continuations.

When the user corrects themselves with an assertive prefix ("I mean", "I meant",
"no, I meant", etc.) plus a scope-broadening word ("whole", "all", "everything",
"full", "total", "entire"), they intend to drop the active narrow filter and
keep only the period and money direction.
"""

from __future__ import annotations

from typing import Any

from banking.transactions.query.continuations.transforms import rebuild_query_request
from banking.transactions.query.models.domain import Aggregation, Filters, QueryIntent
from banking.transactions.query.models.operations import QueryRequest
from banking.transactions.shared.correction_markers import is_scope_broadening_correction


def drop_narrow_scope_filters(filters: Filters | None) -> Filters | None:
    """Return filters with bucket filters removed but direction/amount/status preserved."""
    if filters is None:
        return None
    return filters.model_copy(
        update={
            "category": None,
            "merchant": None,
            "counterparty": None,
            "exclude": None,
            "account_filter": None,
        }
    )


def _is_broadenable_session(session_query_request: QueryRequest | None) -> bool:
    return bool(
        session_query_request
        and session_query_request.intent
        in {
            QueryIntent.TRANSACTION_LIST,
            QueryIntent.TRANSACTION_SEARCH,
            QueryIntent.ANALYTICS_SUMMARY,
            QueryIntent.BENEFICIARY_SUMMARY,
            QueryIntent.CASH_FLOW_SUMMARY,
        }
    )


async def maybe_recover_scope_broadening_continuation(
    *,
    message: str,
    session_query_request: QueryRequest | None,
) -> dict[str, Any] | None:
    """Rebuild an aggregate total over the active period when the user broadens scope."""
    if session_query_request is None or not _is_broadenable_session(session_query_request):
        return None
    if not is_scope_broadening_correction(message):
        return None

    broadened_filters = drop_narrow_scope_filters(session_query_request.filters)
    query_request = rebuild_query_request(
        session_query_request,
        intent=QueryIntent.ANALYTICS_SUMMARY,
        filters=broadened_filters,
        merge_filters=False,
        aggregation=Aggregation(type="sum"),
        result_limit=None,
        result_reference=None,
        answer_fact_field=None,
        continuation_type="aggregate",
        continuation_delta_type="filter",
    )
    return {
        "flow_state": "executing",
        "continuation_type": "aggregate",
        "continuation_delta_type": "filter",
        "resolver_message": None,
        "query_request": query_request,
        "current_page": 0,
        "show_expanded": False,
        "session_active": True,
    }
