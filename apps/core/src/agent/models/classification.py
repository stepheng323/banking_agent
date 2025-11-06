"""Model definitions for the orchestrator agent."""
from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    """Result for the classification task."""
    response: str = Field(description="The response to the user's message.")
    intent: str = Field(
        description="The intent of the user's message.")
    is_complex: bool = Field(
        description="Whether the user's message is complex.")
    complexity_reason: str = Field(
        description="The reason for the complexity of the user's message.")
    confidence: float = Field(
        description="How sure in percentage you, about this classification")
