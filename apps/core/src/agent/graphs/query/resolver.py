"""Query resolver.

Handles negotiation and clamping for query capabilities.
- Checks requested_capabilities against QUERY_SUPPORTS
- Negotiates down (TIME_ALL → TIME_RELATIVE within limits)
- Clamps values (time range, result count, etc.)
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.query.capabilities import (
    QueryCapability,
    QUERY_SUPPORTS,
    QUERY_LIMITS,
    get_alternative,
    generate_limitation_message,
)
from apps.core.src.agent.graphs.query.models_extraction import (
    QueryExtractionResult,
    RequestedCapability,
    TimeReference,
    Ambiguity,
    AmbiguityCode,
)


class Decision(str, Enum):
    """Resolver decision for flow control."""
    
    PROCEED = "PROCEED"           
    NEGOTIATE = "NEGOTIATE"       
    ASK_CLARIFY = "ASK_CLARIFY"   
    REJECT = "REJECT"             


class Negotiation(BaseModel):
    """Negotiation details."""
    
    original_capability: RequestedCapability
    alternative: QueryCapability | None
    message: str
    auto_apply: bool = Field(default=False, description="Can apply without asking")


class Prompt(BaseModel):
    """Templated prompt for response."""
    
    key: str
    vars: dict[str, Any] = Field(default_factory=dict)


class ClampedValues(BaseModel):
    """Values that were clamped to limits."""
    
    days_back: int | None = Field(default=None, description="Clamped to max_lookback_days")
    result_limit: int | None = Field(default=None, description="Clamped to max_results")


class ResolverDecision(BaseModel):
    """Decision contract from resolver."""
    
    decision: Decision
    extraction: QueryExtractionResult
    negotiation: Negotiation | None = Field(default=None)
    ambiguity_to_resolve: Ambiguity | None = Field(default=None)
    clamped: ClampedValues = Field(default_factory=ClampedValues)
    prompts: list[Prompt] = Field(default_factory=list)


CAPABILITY_MAP = {
    RequestedCapability.FILTER_RECIPIENT: QueryCapability.FILTER_RECIPIENT,
    RequestedCapability.FILTER_AMOUNT: QueryCapability.FILTER_AMOUNT,
    RequestedCapability.FILTER_CATEGORY: QueryCapability.FILTER_CATEGORY,
    RequestedCapability.FILTER_TX_TYPE: QueryCapability.FILTER_TX_TYPE,
    RequestedCapability.FILTER_BANK: QueryCapability.FILTER_BANK,
    RequestedCapability.SEARCH_NARRATION_KEYWORD: QueryCapability.SEARCH_NARRATION_KEYWORD,
    RequestedCapability.SEARCH_NARRATION_FUZZY: QueryCapability.SEARCH_NARRATION_FUZZY,
    RequestedCapability.TIME_RELATIVE: QueryCapability.TIME_RELATIVE,
    RequestedCapability.TIME_ALL: QueryCapability.TIME_ALL,
    RequestedCapability.AGGREGATE_SUM: QueryCapability.AGGREGATE_SUM,
    RequestedCapability.AGGREGATE_GROUP: QueryCapability.AGGREGATE_GROUP,
    RequestedCapability.TIME_COMPARISON: QueryCapability.TIME_COMPARISON,
    RequestedCapability.EXPORT_PDF: QueryCapability.EXPORT_PDF,
    RequestedCapability.EXPORT_CSV: QueryCapability.EXPORT_CSV,
}


def check_capabilities(requested: list[RequestedCapability]) -> list[QueryCapability]:
    """Check which requested capabilities are not supported."""
    missing = []
    for req in requested:
        cap = CAPABILITY_MAP.get(req)
        if cap and cap not in QUERY_SUPPORTS:
            missing.append(cap)
    return missing


def clamp_time_range(extraction: QueryExtractionResult) -> tuple[QueryExtractionResult, int | None]:
    """Clamp time range to max_lookback_days. Returns (updated, clamped_days)."""
    if extraction.time_range.reference_type == TimeReference.ALL_TIME:
        extraction.time_range.days_back = QUERY_LIMITS["max_lookback_days"]
        extraction.time_range.reference_type = TimeReference.EXPLICIT
        return extraction, QUERY_LIMITS["max_lookback_days"]
    
    if extraction.time_range.days_back and extraction.time_range.days_back > QUERY_LIMITS["max_lookback_days"]:
        original = extraction.time_range.days_back
        extraction.time_range.days_back = QUERY_LIMITS["max_lookback_days"]
        return extraction, QUERY_LIMITS["max_lookback_days"]
    
    if extraction.time_range.reference_type == TimeReference.VAGUE:
        extraction.time_range.days_back = QUERY_LIMITS["default_lookback_days"]
        return extraction, None
    
    return extraction, None


def resolve(extraction: QueryExtractionResult) -> ResolverDecision:
    """Main resolver entry point."""
    
    if extraction.ambiguities:
        time_vague = next(
            (a for a in extraction.ambiguities if a.code == AmbiguityCode.TIME_VAGUE),
            None
        )
        if time_vague:
            return ResolverDecision(
                decision=Decision.ASK_CLARIFY,
                extraction=extraction,
                ambiguity_to_resolve=time_vague,
                prompts=[Prompt(
                    key="query.time_vague",
                    vars={"context": time_vague.context, "suggestion": f"last {QUERY_LIMITS['default_lookback_days']} days"},
                )],
            )
    
    # Check capabilities
    missing = check_capabilities(extraction.requested_capabilities)
    
    if missing:
        cap = missing[0]
        alt = get_alternative(cap)
        
        # Can we auto-negotiate?
        if cap == QueryCapability.TIME_ALL:
            # Auto-clamp to max and negotiate
            extraction, clamped_days = clamp_time_range(extraction)
            return ResolverDecision(
                decision=Decision.NEGOTIATE,
                extraction=extraction,
                negotiation=Negotiation(
                    original_capability=RequestedCapability.TIME_ALL,
                    alternative=QueryCapability.TIME_RELATIVE,
                    message=generate_limitation_message([cap]),
                    auto_apply=False,  # Ask user first
                ),
                clamped=ClampedValues(days_back=clamped_days),
            )
        
        if cap == QueryCapability.SEARCH_NARRATION_FUZZY:
            return ResolverDecision(
                decision=Decision.NEGOTIATE,
                extraction=extraction,
                negotiation=Negotiation(
                    original_capability=RequestedCapability.SEARCH_NARRATION_FUZZY,
                    alternative=QueryCapability.SEARCH_NARRATION_KEYWORD,
                    message=generate_limitation_message([cap]),
                    auto_apply=False,
                ),
            )
        
        # No alternative available
        if cap in (QueryCapability.EXPORT_PDF, QueryCapability.EXPORT_CSV):
            return ResolverDecision(
                decision=Decision.NEGOTIATE,
                extraction=extraction,
                negotiation=Negotiation(
                    original_capability=RequestedCapability.EXPORT_PDF if cap == QueryCapability.EXPORT_PDF else RequestedCapability.EXPORT_CSV,
                    alternative=None,
                    message=generate_limitation_message([cap]),
                    auto_apply=False,
                ),
            )
    
    # Clamp time if needed (even for supported queries)
    extraction, clamped_days = clamp_time_range(extraction)
    
    return ResolverDecision(
        decision=Decision.PROCEED,
        extraction=extraction,
        clamped=ClampedValues(days_back=clamped_days) if clamped_days else ClampedValues(),
    )
