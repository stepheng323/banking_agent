"""Compatibility adapters for assistant profile prompt and meta behavior."""

from __future__ import annotations

from typing import Any

from shared.assistant_profile.models import AssistantProfile
from shared.assistant_profile.voice import build_meta_voice_payload, build_planner_voice_block, get_runtime_voice


def build_system_profile(profile: AssistantProfile) -> dict[str, Any]:
    return get_runtime_voice(profile=profile).as_system_payload()


def build_planner_profile_summary(profile: AssistantProfile) -> str:
    return build_planner_voice_block(profile)


def build_meta_profile_payload(profile: AssistantProfile) -> dict[str, Any]:
    return build_meta_voice_payload(profile)
