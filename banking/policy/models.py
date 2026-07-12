"""Typed models for runtime capability policy."""

from pydantic import BaseModel, Field


class CapabilityRule(BaseModel):
    """Capability support and fallback rule for an action."""

    supported: bool = True
    alternative: str | None = None
    limitation_message: str | None = None


class DomainCapabilityPolicy(BaseModel):
    """Per-domain capability map."""

    domain: str
    enabled: bool = True
    limitation_message: str | None = None
    actions: dict[str, CapabilityRule] = Field(default_factory=dict)


class ConversationalSuggestion(BaseModel):
    """A user-facing suggestion tied to policy capabilities."""

    id: str
    domain: str
    action: str
    label_key: str


class AvailableConversationalSuggestion(BaseModel):
    """A policy-approved suggestion ready for conversational presentation."""

    id: str
    label: str


class CapabilityPolicy(BaseModel):
    """Top-level runtime capability policy loaded from JSON."""

    version: str = "1.0.0"
    last_updated: str | None = None
    capability_matrix: dict[str, DomainCapabilityPolicy] = Field(default_factory=dict)
    conversational_suggestions: list[ConversationalSuggestion] = Field(default_factory=list)
