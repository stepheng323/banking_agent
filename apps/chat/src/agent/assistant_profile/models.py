"""Typed models for assistant profile configuration."""

from pydantic import BaseModel, Field


class AssistantIdentity(BaseModel):
    """Identity metadata for the assistant."""

    name: str
    description: str
    positioning: str
    creator: str | None = None
    brand_origin: str | None = None


class AssistantTone(BaseModel):
    """Tone and response behavior."""

    style: str
    brevity: str
    response_rules: list[str] = Field(default_factory=list)


class AssistantProfile(BaseModel):
    """Top-level assistant profile loaded from JSON."""

    version: str = "1.0.0"
    last_updated: str | None = None
    identity: AssistantIdentity
    tone: AssistantTone
    supported_domains: list[str] = Field(default_factory=list)
    unsupported_capabilities: list[str] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
