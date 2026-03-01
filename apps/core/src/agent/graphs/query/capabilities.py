"""Query graph capability definitions.

Capabilities and limits for resolver-driven negotiation.
LLM outputs requested_capabilities, resolver checks and negotiates.
"""

from enum import Enum

from shared.i18n import render_capability_limitation
from shared.policy.adapters import resolve_capability_alternative, resolve_capability_rule


class QueryCapability(str, Enum):
    """Capabilities that can be required by a query plan."""

    # Filters
    FILTER_RECIPIENT = "filter_recipient"
    FILTER_AMOUNT = "filter_amount"
    FILTER_CATEGORY = "filter_category"
    FILTER_TX_TYPE = "filter_tx_type"
    FILTER_BANK = "filter_bank"

    # Search - split for clarity
    SEARCH_NARRATION_KEYWORD = "search_narration_keyword"  # Exact/contains match
    SEARCH_NARRATION_FUZZY = "search_narration_fuzzy"  # Semantic/embeddings

    # Time ranges
    TIME_RELATIVE = "time_relative"  # Within limits
    TIME_ALL = "time_all"  # Beyond max_lookback_days

    # Aggregations
    AGGREGATE_SUM = "aggregate_sum"
    AGGREGATE_GROUP = "aggregate_group"
    TIME_COMPARISON = "time_comparison"

    # Exports
    EXPORT_PDF = "export_pdf"
    EXPORT_CSV = "export_csv"


# Limits for resolver negotiation (clamp instead of fail)
QUERY_LIMITS = {
    "max_lookback_days": 180,
    "max_results": 50,
    "max_group_buckets": 20,
    "max_narration_query_len": 40,
    "default_lookback_days": 30,
}


CAPABILITY_LABELS: dict[QueryCapability, str] = {
    QueryCapability.FILTER_RECIPIENT: "filter by recipient",
    QueryCapability.FILTER_AMOUNT: "filter by amount",
    QueryCapability.FILTER_CATEGORY: "filter by category",
    QueryCapability.FILTER_TX_TYPE: "filter by type",
    QueryCapability.FILTER_BANK: "filter by bank",
    QueryCapability.SEARCH_NARRATION_KEYWORD: "keyword search",
    QueryCapability.SEARCH_NARRATION_FUZZY: "fuzzy search",
    QueryCapability.TIME_RELATIVE: "time range",
    QueryCapability.TIME_ALL: "all-time queries",
    QueryCapability.AGGREGATE_SUM: "totals",
    QueryCapability.AGGREGATE_GROUP: "breakdowns",
    QueryCapability.TIME_COMPARISON: "period comparison",
    QueryCapability.EXPORT_PDF: "PDF export",
    QueryCapability.EXPORT_CSV: "CSV export",
}


def check_capabilities(requires: list[QueryCapability]) -> list[QueryCapability]:
    """Check which required capabilities are missing.

    Policy is authoritative: if a capability has no rule, treat it as unsupported.
    """
    missing: list[QueryCapability] = []
    for cap in requires:
        policy_rule = resolve_capability_rule(domain="query", action=cap.value)
        if policy_rule is None or not policy_rule.supported:
            missing.append(cap)
    return missing


def get_alternative(cap: QueryCapability) -> QueryCapability | None:
    """Get alternative capability for a missing one."""
    policy_alternative = resolve_capability_alternative(domain="query", action=cap.value)
    if not policy_alternative:
        return None
    try:
        return QueryCapability(policy_alternative)
    except ValueError:
        return None


def generate_limitation_message(missing: list[QueryCapability], *, locale: str = "en") -> str:
    """Generate conversational negotiation message."""
    if not missing:
        return ""

    cap = missing[0]

    alt = get_alternative(cap)
    label = CAPABILITY_LABELS.get(cap, cap.value)
    alt_label = CAPABILITY_LABELS.get(alt, alt.value) if alt else None

    return render_capability_limitation(
        locale=locale,
        action_label=label,
        alternative_labels=[alt_label] if alt_label else [],
    )
