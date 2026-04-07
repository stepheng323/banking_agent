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
    actions: dict[str, CapabilityRule] = Field(default_factory=dict)


class CapabilityPolicy(BaseModel):
    """Top-level runtime capability policy loaded from JSON."""

    version: str = "1.0.0"
    last_updated: str | None = None
    capability_matrix: dict[str, DomainCapabilityPolicy] = Field(default_factory=dict)
