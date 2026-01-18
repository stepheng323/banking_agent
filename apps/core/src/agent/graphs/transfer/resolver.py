"""Transfer extraction resolver.

Handles business logic after LLM extraction:
- Computes missing fields from schema
- Checks capabilities (requested features)
- Resolves references (recent transfers)
- Returns Decision contract for flow control
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.transfer.models import TransferEntities
from apps.core.src.agent.graphs.transfer.models_extraction import (
    Ambiguity,
    AmbiguityCode,
    References,
    RequestedFeature,
    TransferExtractionResult,
)
from apps.core.src.agent.graphs.transfer.capabilities import (
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
    
    key: str = Field(description="Template key, e.g., 'transfer.ask_account'")
    vars: dict[str, Any] = Field(default_factory=dict, description="Variables for template")


class Negotiation(BaseModel):
    """Negotiation details for unsupported features."""
    
    feature: RequestedFeature = Field(description="Unsupported feature")
    message_key: str = Field(description="Template key for negotiation message")


class ResolverDecision(BaseModel):
    """Decision contract from resolver."""
    
    decision: Decision = Field(description="Flow control decision")
    missing_fields: list[str] = Field(default_factory=list, description="Fields still needed")
    applied_entities: TransferEntities | None = Field(default=None, description="Entities after resolution")
    negotiation: Negotiation | None = Field(default=None, description="If NEGOTIATE decision")
    limitation_message: str | None = Field(default=None, description="Message explaining limitation")
    prompts: list[Prompt] = Field(default_factory=list, description="Prompts for formatter")
    ambiguity_to_resolve: Ambiguity | None = Field(default=None, description="Ambiguity needing clarification")
    suggested_action: str | None = Field(default=None, description="Suggested action if user accepts")
    patch: dict[str, Any] = Field(default_factory=dict, description="State changes if user accepts")


REQUIRED_EXTERNAL = ["amount", "recipient_account", "bank_name"]
REQUIRED_INTERNAL = ["amount", "bank_name"]


def compute_missing_fields(entities: TransferEntities | None, is_internal: bool = False) -> list[str]:
    """Compute missing fields from entity schema."""
    if not entities:
        return ["amount", "recipient_account", "bank_name"]
    
    missing = []
    required = REQUIRED_INTERNAL if is_internal else REQUIRED_EXTERNAL
    
    for field in required:
        value = getattr(entities, field, None)
        if value is None:
            if field == "amount" and (entities.transfer_all or entities.transfer_percentage):
                continue
            missing.append(field)
    
    return missing


def resolve_references(
    entities: TransferEntities | None,
    references: References,
    recent_transfers: list[dict] | None,
    trigger_phrases: list[str] | None = None,
) -> tuple[TransferEntities | None, bool]:
    """
    Resolve references to recent transfers.
    
    Only hydrates if user explicitly indicated (trigger phrase).
    Returns (updated_entities, was_hydrated).
    """
    if not references.use_recent_transfer:
        return entities, False
    
    if not recent_transfers:
        return entities, False
    
    idx = references.recent_transfer_index or 0
    if idx >= len(recent_transfers):
        return entities, False
    
    recent = recent_transfers[idx]
    
    if entities is None:
        entities = TransferEntities()
    
    if entities.recipient_account is None and "recipient_account" in recent:
        entities.recipient_account = recent["recipient_account"]
    if entities.bank_name is None and "bank_name" in recent:
        entities.bank_name = recent["bank_name"]
    if entities.recipient_name is None and "recipient_name" in recent:
        entities.recipient_name = recent["recipient_name"]
    
    return entities, True


def check_requested_features(features: list[RequestedFeature]) -> Negotiation | None:
    """Check if requested features are supported."""
    unsupported_map = {
        RequestedFeature.SCHEDULED: TransferCapability.SCHEDULED,
        RequestedFeature.RECURRING: TransferCapability.RECURRING,
        RequestedFeature.INTERNATIONAL: TransferCapability.INTERNATIONAL,
    }
    
    for feature in features:
        if feature in unsupported_map:
            cap = unsupported_map[feature]
            missing = check_capabilities([cap])
            if missing:
                return Negotiation(
                    feature=feature,
                    message_key=f"transfer.{feature.value.lower()}_not_available",
                )
    
    return None


def resolve(
    extraction: TransferExtractionResult,
    draft_entities: TransferEntities | None = None,
    recent_transfers: list[dict] | None = None,
    user_message: str = "",
) -> ResolverDecision:
    """
    Main resolver entry point.
    
    Args:
        extraction: LLM extraction result
        draft_entities: Existing draft from previous turns
        recent_transfers: User's recent transfer history
        user_message: Original user message
        
    Returns:
        ResolverDecision with flow control
    """
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
        ordered_codes = [
            AmbiguityCode.AMOUNT_UNCLEAR,
            AmbiguityCode.MULTIPLE_BENEFICIARIES,
            AmbiguityCode.UNCLEAR_BANK,
            AmbiguityCode.UNCLEAR_RECIPIENT,
        ]
        
        for code in ordered_codes:
            ambiguity = next((a for a in extraction.ambiguities if a.code == code), None)
            if ambiguity:
                prompt_key = None
                if code == AmbiguityCode.AMOUNT_UNCLEAR:
                    prompt_key = "transfer.amount_ambiguous"
                elif code == AmbiguityCode.MULTIPLE_BENEFICIARIES:
                    prompt_key = "transfer.ambiguous_beneficiary"
                elif code == AmbiguityCode.UNCLEAR_BANK:
                    prompt_key = "transfer.ambiguous_bank"
                elif code == AmbiguityCode.UNCLEAR_RECIPIENT:
                    prompt_key = "transfer.ambiguous_recipient"
                
                if prompt_key:
                    return ResolverDecision(
                        decision=Decision.ASK_CLARIFY,
                        missing_fields=[code.name], # Use ambiguity code as pseudo-missing field
                        applied_entities=entities,
                        ambiguity_to_resolve=ambiguity,
                        prompts=[Prompt(
                            key=prompt_key,
                            vars={"candidates": ambiguity.candidates},
                        )],
                    )
    
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
    
    entities, was_hydrated = resolve_references(
        entities,
        extraction.references,
        recent_transfers,
    )
    
    is_internal = bool(entities and entities.source_bank_name and entities.bank_name and not entities.recipient_name)
    missing = compute_missing_fields(entities, is_internal)
    
    if missing:
        prompts = []
        if "recipient_account" in missing:
            prompts.append(Prompt(
                key="transfer.ask_account",
                vars={"recipient": entities.recipient_name if entities else None, "bank": entities.bank_name if entities else None},
            ))
        elif "amount" in missing:
            prompts.append(Prompt(key="transfer.ask_amount", vars={}))
        elif "bank_name" in missing:
            prompts.append(Prompt(key="transfer.ask_bank", vars={}))
        
        return ResolverDecision(
            decision=Decision.ASK_CLARIFY,
            missing_fields=missing,
            applied_entities=entities,
            prompts=prompts,
        )
    
    return ResolverDecision(
        decision=Decision.PROCEED,
        missing_fields=[],
        applied_entities=entities,
    )
