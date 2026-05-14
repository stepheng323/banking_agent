"""Runtime voice adapter for assistant identity and tone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.assistant_profile.models import AssistantProfile


@dataclass(frozen=True)
class AssistantVoice:
    """Runtime projection of AssistantProfile for prompts and grounded replies."""

    name: str
    description: str
    positioning: str
    creator: str | None
    brand_origin: str | None
    supported_domains: tuple[str, ...]
    unsupported_capabilities: tuple[str, ...]
    tone_style: str
    brevity: str
    response_rules: tuple[str, ...]
    safety_rules: tuple[str, ...]

    @classmethod
    def from_profile(cls, profile: AssistantProfile) -> AssistantVoice:
        return cls(
            name=profile.identity.name,
            description=profile.identity.description,
            positioning=profile.identity.positioning,
            creator=profile.identity.creator,
            brand_origin=profile.identity.brand_origin,
            supported_domains=tuple(profile.supported_domains),
            unsupported_capabilities=tuple(profile.unsupported_capabilities),
            tone_style=profile.tone.style,
            brevity=profile.tone.brevity,
            response_rules=tuple(profile.tone.response_rules),
            safety_rules=tuple(profile.safety_rules),
        )

    def as_system_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "positioning": self.positioning,
            "creator": self.creator,
            "brand_origin": self.brand_origin,
            "supported_domains": list(self.supported_domains),
            "unsupported_capabilities": list(self.unsupported_capabilities),
            "tone": self.tone_style,
            "brevity": self.brevity,
        }

    def as_meta_payload(self) -> dict[str, Any]:
        return self.as_system_payload() | {
            "response_rules": list(self.response_rules),
            "safety_rules": list(self.safety_rules),
        }


def get_runtime_voice(*, profile: AssistantProfile | None = None, force_reload: bool = False) -> AssistantVoice:
    """Return the runtime assistant voice derived from AssistantProfile."""
    effective_profile = profile or get_cached_assistant_profile(force_reload=force_reload)
    return AssistantVoice.from_profile(effective_profile)


def _preview(items: tuple[str, ...]) -> str:
    if not items:
        return "None"
    first = items[0]
    remaining = len(items) - 1
    return f"{first}(+{remaining})" if remaining > 0 else first


def _clip(text: str, *, limit: int = 56) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def build_planner_voice_block(profile: AssistantProfile | None = None) -> str:
    """Build the compact planner voice/profile block."""
    voice = get_runtime_voice(profile=profile)
    tone_rule = voice.response_rules[0] if voice.response_rules else voice.tone_style
    safety_rule = voice.safety_rules[0] if voice.safety_rules else "Prioritize banking/support workflows only"

    return (
        "## PROFILE\n"
        f"- Supported(profile): {_preview(voice.supported_domains)}; "
        f"Unsupported(profile): {_preview(voice.unsupported_capabilities)}.\n"
        f"- Tone/Safety: {_clip(tone_rule, limit=24)} | {_clip(safety_rule, limit=24)}.\n"
        "- OOS->conversational.out_of_scope.\n"
        "- Reply in user language."
    )


def build_meta_voice_payload(profile: AssistantProfile | None = None) -> dict[str, Any]:
    return get_runtime_voice(profile=profile).as_meta_payload()


def build_conversation_voice_block(*, locale: str, channel: str = "WhatsApp") -> str:
    """Build voice instructions for bounded casual conversation prompts."""
    voice = get_runtime_voice()
    response_rules = "; ".join(voice.response_rules) or "Keep replies safe, brief, and grounded."
    safety_rules = "; ".join(voice.safety_rules) or "Stay within banking and support workflows."
    return (
        f"You are {voice.name}, {voice.description}\n"
        f"Channel: {channel}.\n"
        f"Locale: {locale}.\n"
        f"Tone: {voice.tone_style}; {voice.brevity}.\n"
        f"Voice rules: {response_rules}\n"
        f"Safety rules: {safety_rules}\n"
    )
