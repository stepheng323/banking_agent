"""System identity and capability contract for the orchestrator."""

from dataclasses import dataclass

from shared.assistant_profile.adapters import build_system_profile
from shared.assistant_profile.loader import get_cached_assistant_profile


@dataclass(frozen=True)
class SystemProfile:
    """Static profile for identity and capabilities."""

    name: str
    description: str
    positioning: str
    creator: str | None
    brand_origin: str | None
    supported_domains: list[str]
    unsupported_capabilities: list[str]
    tone: str


SYSTEM_PROFILE = SystemProfile(**build_system_profile(get_cached_assistant_profile()))
