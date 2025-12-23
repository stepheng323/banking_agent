"""Model definitions for the orchestrator agent."""
from pydantic import BaseModel, Field
from typing import Optional


class ClassificationResult(BaseModel):
    """Result for the classification task."""
    response: str = Field(description="The response to the user's message.")
    intent: str = Field(
        description="The intent of the user's message (transfer, airtime, data, conversational, cancel, unknown).")
    is_complex: bool = Field(
        description="Whether the user's message is complex.")
    complexity_reason: str = Field(
        description="The reason for the complexity of the user's message.")
    confidence: float = Field(
        description="How sure in percentage you, about this classification")
    is_cancellation: Optional[bool] = Field(
        default=None,
        description="True if the user wants to cancel/abort the current transaction. Set to true when intent is 'cancel' or when there's an active transaction and user expresses cancellation intent."
    )
    extracted_alias: Optional[str] = Field(
        default=None,
        description="If responding to a beneficiary suggestion and user provides an alias/name (e.g., 'save as mum', 'My opay', 'mum'), extract and return the alias here. Otherwise leave null."
    )
    detected_language: Optional[str] = Field(
        default=None,
        description="The detected language of the user's message (e.g., English, Yoruba, Hausa, Igbo, Pidgin, French). Only set this if you are confident about the language."
    )
    task_parameters: Optional[dict] = Field(
        default={},
        description="Extracted key parameters for the intent (e.g., {'amount': 5000, 'recipient': 'mum'} for transfer). Use standard keys: amount, recipient, bank_name, account_number."
    )

