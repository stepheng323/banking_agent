"""Adapters for assistant profile prompt and meta behavior."""

from __future__ import annotations

from typing import Any

from shared.assistant_profile.models import AssistantProfile


def build_system_profile(profile: AssistantProfile) -> dict[str, Any]:
    return {
        "name": profile.identity.name,
        "description": profile.identity.description,
        "positioning": profile.identity.positioning,
        "creator": profile.identity.creator,
        "brand_origin": profile.identity.brand_origin,
        "supported_domains": profile.supported_domains,
        "unsupported_capabilities": profile.unsupported_capabilities,
        "tone": profile.tone.style,
    }


def build_planner_profile_summary(profile: AssistantProfile) -> str:
    def _preview(items: list[str]) -> str:
        if not items:
            return "None"
        first = items[0]
        remaining = len(items) - 1
        return f"{first}(+{remaining})" if remaining > 0 else first

    def _clip(text: str, *, limit: int = 56) -> str:
        compact = " ".join(text.strip().split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    tone_rule = profile.tone.response_rules[0] if profile.tone.response_rules else profile.tone.style
    safety_rule = profile.safety_rules[0] if profile.safety_rules else "Prioritize banking/support workflows only"

    return (
        "## PROFILE\n"
        f"- Supported(profile): {_preview(profile.supported_domains)}; "
        f"Unsupported(profile): {_preview(profile.unsupported_capabilities)}.\n"
        f"- Tone/Safety: {_clip(tone_rule, limit=24)} | {_clip(safety_rule, limit=24)}.\n"
        "- OOS -> conversational.out_of_scope.\n"
        "- Reply in user language."
    )


def build_meta_profile_payload(profile: AssistantProfile) -> dict[str, Any]:
    return build_system_profile(profile) | {
        "response_rules": profile.tone.response_rules,
        "safety_rules": profile.safety_rules,
    }
