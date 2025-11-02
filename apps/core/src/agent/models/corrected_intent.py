"""Models for typo correction and intent normalization."""
from typing import List, Optional
from pydantic import BaseModel, Field


class EntityCorrection(BaseModel):
    """Single entity correction."""
    
    entity: str  # Original text
    corrected_to: str  # Corrected text
    entity_type: str  # beneficiary | bank | amount | instruction
    confidence: float  # 0.0-1.0
    matched_entity_id: Optional[str] = None  # beneficiary_id or account_id if matched


class CorrectedIntent(BaseModel):
    """Result of typo correction with context."""
    
    original: str
    corrected: str
    corrections: List[EntityCorrection] = Field(default_factory=list)
    confidence: float
    needs_clarification: bool
    clarification_question: Optional[str] = None


class DisambiguatedIntent(BaseModel):
    """Result of intent disambiguation."""
    
    normalized_instruction: str
    is_ambiguous: bool
    needs_clarification: bool
    clarification_question: Optional[str] = None
    
    # Multi-recipient details
    is_multi_recipient: bool = False
    recipients: List[dict] = Field(default_factory=list)  # [{"name": str, "amount": float}]
    total_amount: Optional[float] = None
    split_strategy: Optional[str] = None  # "equal" | "explicit" | "proportional"
    
    # Multi-account details
    requires_multi_account: bool = False
    suggested_accounts: List[dict] = Field(default_factory=list)

