"""Typed models for Soul policy."""

from pydantic import BaseModel, Field


class SoulIdentity(BaseModel):
    """Identity metadata for the assistant."""

    name: str
    description: str
    positioning: str


class SoulTone(BaseModel):
    """Tone and response behavior."""

    style: str
    brevity: str
    response_rules: list[str] = Field(default_factory=list)


class CapabilityRule(BaseModel):
    """Capability support and fallback rule for an action."""

    supported: bool = True
    alternative: str | None = None
    limitation_message: str | None = None


class DomainCapabilityPolicy(BaseModel):
    """Per-domain capability map."""

    domain: str
    actions: dict[str, CapabilityRule] = Field(default_factory=dict)


class SoulPolicy(BaseModel):
    """Top-level policy model loaded from JSON policy file."""

    version: str = "1.0.0"
    last_updated: str | None = None
    identity: SoulIdentity
    tone: SoulTone
    supported_domains: list[str] = Field(default_factory=list)
    unsupported_capabilities: list[str] = Field(default_factory=list)
    unsupported_detection: dict[str, list[str]] = Field(default_factory=dict)
    unsupported_alternatives: dict[str, list[str]] = Field(default_factory=dict)
    safety_rules: list[str] = Field(default_factory=list)
    capability_matrix: dict[str, DomainCapabilityPolicy] = Field(default_factory=dict)
