"""Query graph capability definitions.

Defines what the query graph supports and doesn't support.
LLM outputs requires[] from QueryCapability enum.
Resolver checks against QUERY_SUPPORTS before execution.
"""

from enum import Enum


class QueryCapability(str, Enum):
    """Capabilities that can be required by a query plan."""

    # Filtering
    FILTER_RECIPIENT = "filter_recipient"
    FILTER_AMOUNT = "filter_amount"
    FILTER_CATEGORY = "filter_category"
    FILTER_TX_TYPE = "filter_tx_type"
    FILTER_BANK = "filter_bank"
    SEARCH_NARRATION = "search_narration"

    # Time
    TIME_RELATIVE = "time_relative"
    TIME_ALL = "time_all"

    # Aggregation
    AGGREGATE_SUM = "aggregate_sum"
    AGGREGATE_GROUP = "aggregate_group"
    TIME_COMPARISON = "time_comparison"

    # Export
    EXPORT_PDF = "export_pdf"
    EXPORT_CSV = "export_csv"


QUERY_SUPPORTS: list[QueryCapability] = [
    QueryCapability.FILTER_RECIPIENT,
    QueryCapability.FILTER_AMOUNT,
    QueryCapability.FILTER_CATEGORY,
    QueryCapability.FILTER_TX_TYPE,
    QueryCapability.FILTER_BANK,
    QueryCapability.SEARCH_NARRATION,
    QueryCapability.TIME_RELATIVE,
    QueryCapability.AGGREGATE_SUM,
    QueryCapability.AGGREGATE_GROUP,
    QueryCapability.TIME_COMPARISON,
]


QUERY_LIMITS = {
    "max_lookback_days": 180,
    "max_results": 50,
}


CAPABILITY_LABELS: dict[QueryCapability, str] = {
    QueryCapability.FILTER_RECIPIENT: "filter by recipient",
    QueryCapability.FILTER_AMOUNT: "filter by amount",
    QueryCapability.FILTER_CATEGORY: "filter by category",
    QueryCapability.FILTER_TX_TYPE: "filter by type",
    QueryCapability.FILTER_BANK: "filter by bank",
    QueryCapability.SEARCH_NARRATION: "search by description",
    QueryCapability.TIME_RELATIVE: "time range",
    QueryCapability.TIME_ALL: "all-time queries",
    QueryCapability.AGGREGATE_SUM: "totals",
    QueryCapability.AGGREGATE_GROUP: "breakdowns",
    QueryCapability.TIME_COMPARISON: "period comparison",
    QueryCapability.EXPORT_PDF: "PDF export",
    QueryCapability.EXPORT_CSV: "CSV export",
}


CAPABILITY_ALTERNATIVES: dict[QueryCapability, list[QueryCapability]] = {
    QueryCapability.TIME_ALL: [QueryCapability.TIME_RELATIVE],
    QueryCapability.EXPORT_PDF: [],
    QueryCapability.EXPORT_CSV: [],
}


def check_capabilities(requires: list[QueryCapability]) -> list[QueryCapability]:
    """
    Check which required capabilities are missing.

    Returns:
        List of missing capabilities (empty if all supported)
    """
    return [cap for cap in requires if cap not in QUERY_SUPPORTS]


def get_alternatives(missing: list[QueryCapability]) -> list[QueryCapability]:
    """Get alternative capabilities for missing ones."""
    alternatives = []
    for cap in missing:
        alts = CAPABILITY_ALTERNATIVES.get(cap, [])
        alternatives.extend(alts)
    return alternatives


def derive_requirements(query: "NormalizedQuery") -> list[QueryCapability]:
    """
    Derive required capabilities from a NormalizedQuery.

    Examines the query structure to determine what capabilities
    are needed to execute it.
    """
    from apps.core.src.agent.graphs.query.models import QueryIntent

    requires: list[QueryCapability] = []

    # Check filters
    if query.filters:
        if query.filters.merchant:
            requires.append(QueryCapability.SEARCH_NARRATION)
        if query.filters.min_amount or query.filters.max_amount:
            requires.append(QueryCapability.FILTER_AMOUNT)
        if query.filters.category:
            requires.append(QueryCapability.FILTER_CATEGORY)
        if query.filters.transaction_type:
            requires.append(QueryCapability.FILTER_TX_TYPE)
        if query.filters.account_filter:
            requires.append(QueryCapability.FILTER_BANK)

    # Check time range - if very old or all_time requested
    if query.time_range:
        from datetime import date

        today = date.today()
        days_back = (today - query.time_range.start).days
        if days_back > QUERY_LIMITS["max_lookback_days"]:
            requires.append(QueryCapability.TIME_ALL)
        else:
            requires.append(QueryCapability.TIME_RELATIVE)

    # Check aggregation
    if query.aggregation:
        if query.aggregation.type in ("sum", "average", "count", "largest"):
            requires.append(QueryCapability.AGGREGATE_SUM)
        if query.aggregation.group_by:
            requires.append(QueryCapability.AGGREGATE_GROUP)

    # Check intent
    if query.intent == QueryIntent.TIME_COMPARISON:
        requires.append(QueryCapability.TIME_COMPARISON)

    return list(set(requires))  # Dedupe


# Type hint import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.query.models import NormalizedQuery

