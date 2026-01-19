"""Data extraction resolver.

Handles business logic after LLM extraction:
- Computes missing fields from schema
- Checks capabilities (requested features)
- Returns Decision contract for flow control
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.data.models_extraction import (
    DataPurchaseEntities,
    Ambiguity,
    AmbiguityCode,
    RequestedFeature,
    DataExtractionResult,
)
from apps.core.src.agent.graphs.data.capabilities import CapabilityDecision



class Decision(str, Enum):
    """Resolver decision for flow control."""
    
    PROCEED = "PROCEED"
    ASK_CLARIFY = "ASK_CLARIFY"
    NEGOTIATE = "NEGOTIATE"
    CANCEL = "CANCEL"


class Prompt(BaseModel):
    """Templated prompt for formatter."""
    
    key: str = Field(description="Template key")
    vars: dict[str, Any] = Field(default_factory=dict)


class Negotiation(BaseModel):
    """Negotiation details for unsupported features."""
    
    feature: RequestedFeature = Field(description="Unsupported feature")
    message_key: str = Field(description="Template key for negotiation message")


class ResolverDecision(BaseModel):
    """Decision contract from resolver."""
    
    decision: Decision
    missing_fields: list[str] = Field(default_factory=list)
    applied_entities: DataPurchaseEntities | None = Field(default=None)
    negotiation: Negotiation | None = Field(default=None)
    prompts: list[Prompt] = Field(default_factory=list)
    ambiguity_to_resolve: Ambiguity | None = Field(default=None)
    limitation_message: str | None = Field(default=None)
    suggested_action: str | None = Field(default=None)
    patch: dict[str, Any] = Field(default_factory=dict)


REQUIRED_FIELDS = ["budget", "recipient_phone", "network"]


def compute_missing_fields(entities: DataPurchaseEntities | None, is_self: bool = False) -> list[str]:
    """Compute missing fields from entity schema."""
    if not entities:
        return ["budget", "recipient_phone", "network"]
    
    missing = []
    
    # Budget is always required (or size_preference)
    if entities.budget is None and entities.size_preference is None:
        missing.append("budget")
    
    # Phone and network not required if is_self (user's own line)
    if not is_self and not entities.is_self:
        if entities.recipient_phone is None:
            missing.append("recipient_phone")
        if entities.network is None:
            missing.append("network")
    
    return missing


def check_requested_features(features: list[RequestedFeature]) -> Negotiation | None:
    """Check if requested features are supported."""
    unsupported_map = {
        RequestedFeature.SCHEDULED: DataCapability.SCHEDULED,
        RequestedFeature.RECURRING: DataCapability.RECURRING,
    }
    
    for feature in features:
        if feature in unsupported_map:
            cap = unsupported_map[feature]
            missing = check_capabilities([cap])
            if missing:
                return Negotiation(
                    feature=feature,
                    message_key=f"data.{feature.value.lower()}_not_available",
                )
    
    return None


def resolve(
    extraction: DataExtractionResult,
    draft_entities: DataPurchaseEntities | None = None,
) -> ResolverDecision:
    """Main resolver entry point."""
    entities = extraction.entities
    
    if draft_entities and entities:
        for field in entities.model_fields:
            new_val = getattr(entities, field, None)
            if new_val is not None:
                setattr(draft_entities, field, new_val)
        entities = draft_entities
    elif draft_entities and not entities:
        entities = draft_entities
    
    if extraction.ambiguities:
        budget_ambiguity = next(
            (a for a in extraction.ambiguities if a.code == AmbiguityCode.BUDGET_UNCLEAR),
            None
        )
        if budget_ambiguity:
            return ResolverDecision(
                decision=Decision.ASK_CLARIFY,
                missing_fields=["budget"],
                applied_entities=entities,
                ambiguity_to_resolve=budget_ambiguity,
                prompts=[Prompt(key="data.budget_ambiguous", vars={"candidates": budget_ambiguity.candidates})],
            )
    
    is_self = bool(entities and entities.is_self)
    missing = compute_missing_fields(entities, is_self)
    
    if missing:
        return ResolverDecision(
            decision=Decision.ASK_CLARIFY,
            missing_fields=missing,
            applied_entities=entities,
            prompts=[Prompt(key="data.ask_missing", vars={"fields": missing})],
        )
    
    return ResolverDecision(
        decision=Decision.PROCEED,
        missing_fields=[],
        applied_entities=entities,
    )
