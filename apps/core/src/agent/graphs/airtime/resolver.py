"""Airtime extraction resolver.

Handles business logic after LLM extraction:
- Computes missing fields from schema
- Checks capabilities (requested features)
- Returns Decision contract for flow control
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.airtime.models import (
    SimpleAirtimeEntities,
    Ambiguity,
    AmbiguityCode,
    References,
    RequestedFeature,
    AirtimeExtractionResult,
)
from apps.core.src.agent.graphs.airtime.capabilities import (
    CapabilityDecision,
    decide_capability,
    derive_requirements,
)


class Decision(str, Enum):
    """Resolver decision for flow control."""
    
    PROCEED = "PROCEED"
    ASK_CLARIFY = "ASK_CLARIFY"
    NEGOTIATE = "NEGOTIATE"
    CANCEL = "CANCEL"
    LIMITATION = "LIMITATION"


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
    applied_entities: SimpleAirtimeEntities | None = Field(default=None)
    negotiation: Negotiation | None = Field(default=None)
    limitation_message: str | None = Field(default=None)
    prompts: list[Prompt] = Field(default_factory=list)
    ambiguity_to_resolve: Ambiguity | None = Field(default=None)
    suggested_action: str | None = Field(default=None)
    patch: dict[str, Any] = Field(default_factory=dict)


REQUIRED_FIELDS = ["amount", "recipient_phone", "network"]


def compute_missing_fields(entities: SimpleAirtimeEntities | None, is_self: bool = False) -> list[str]:
    """Compute missing fields from entity schema."""
    if not entities:
        return ["amount", "recipient_phone", "network"]
    
    missing = []
    
    if entities.amount is None:
        missing.append("amount")
    
    if not is_self and not entities.is_self:
        if entities.recipient_phone is None:
            missing.append("recipient_phone")
        if entities.network is None:
            missing.append("network")
    
    return missing


def check_requested_features(features: list[RequestedFeature]) -> Negotiation | None:
    """Check if requested features are supported."""
    unsupported_map = {
        RequestedFeature.SCHEDULED: AirtimeCapability.SCHEDULED,
        RequestedFeature.RECURRING: AirtimeCapability.RECURRING,
    }
    
    for feature in features:
        if feature in unsupported_map:
            cap = unsupported_map[feature]
            missing = check_capabilities([cap])
            if missing:
                return Negotiation(
                    feature=feature,
                    message_key=f"airtime.{feature.value.lower()}_not_available",
                )
    
    return None


def resolve(
    extraction: AirtimeExtractionResult,
    draft_entities: SimpleAirtimeEntities | None = None,
    user_message: str = "",
) -> ResolverDecision:
    """Main resolver entry point."""
    entities = extraction.entities
    
    # Merge with draft
    if draft_entities and entities:
        for field in entities.model_fields:
            new_val = getattr(entities, field, None)
            if new_val is not None:
                setattr(draft_entities, field, new_val)
        entities = draft_entities
    elif draft_entities and not entities:
        entities = draft_entities
    
    # Check ambiguities
    if extraction.ambiguities:
        amount_ambiguity = next(
            (a for a in extraction.ambiguities if a.code == AmbiguityCode.AMOUNT_UNCLEAR),
            None
        )
        if amount_ambiguity:
            return ResolverDecision(
                decision=Decision.ASK_CLARIFY,
                missing_fields=["amount"],
                applied_entities=entities,
                ambiguity_to_resolve=amount_ambiguity,
                prompts=[Prompt(key="airtime.amount_ambiguous", vars={"candidates": amount_ambiguity.candidates})],
            )
    
    # Check capabilities and constraints
    requires = derive_requirements(extraction, user_message)
    cap_decision = decide_capability(requires, extraction)
    
    if not cap_decision.allowed:
        return ResolverDecision(
            decision=Decision.NEGOTIATE if cap_decision.suggested_action else Decision.LIMITATION,
            applied_entities=entities,
            limitation_message=cap_decision.prompt,
            suggested_action=cap_decision.suggested_action,
            patch=cap_decision.patch,
        )
    
    # Compute missing fields
    is_self = bool(entities and entities.is_self)
    missing = compute_missing_fields(entities, is_self)
    
    if missing:
        return ResolverDecision(
            decision=Decision.ASK_CLARIFY,
            missing_fields=missing,
            applied_entities=entities,
            prompts=[Prompt(key="airtime.ask_missing", vars={"fields": missing})],
        )
    
    return ResolverDecision(
        decision=Decision.PROCEED,
        missing_fields=[],
        applied_entities=entities,
    )
