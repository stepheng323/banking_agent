"""Classification models for intent detection and routing."""
from typing import Literal

from pydantic import BaseModel


class UnifiedClassification(BaseModel):
    """Unified output that handles continuation detection and intent classification.

    This model is used in a single LLM call to:
    1. Determine if a message is a continuation of an active conversation
    2. Classify the intent (query, transfer, utility) if it's a new request
    """

    is_continuation: bool = False
    intent: Literal["query", "transfer", "utility"]
    confidence: float
    reasoning: str
