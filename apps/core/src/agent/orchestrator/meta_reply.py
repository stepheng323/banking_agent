"""Meta reply utilities for identity/capability and conversational responses."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.system_profile import SYSTEM_PROFILE, SystemProfile


class MetaReply(BaseModel):
    """Output for meta identity/capability replies."""

    handoff: Literal["meta", "domain"] = Field(description="Whether to answer or route to domain")
    language: Literal["en", "yo", "pcm", "ha"] = Field(description="Response language")
    message: str = Field(description="Response message")


META_SYSTEM_PROMPT = (
    "You are the Meta Reply module for a banking assistant.\n"
    "Your job: answer user questions about identity, name origin, help, capabilities, and basic social niceties.\n"
    "You MUST follow the provided system_profile strictly.\n\n"
    "Rules:\n"
    "- Never invent capabilities. Only mention items in system_profile.supported_domains.\n"
    "- When listing capabilities, use the items in system_profile.supported_domains verbatim.\n"
    "- If asked about something not supported, say it's not available yet and suggest a supported alternative.\n"
    "- If asked about something unrelated to banking, say you focus on the supported domains and offer those.\n"
    "- Keep responses short (max 6 lines).\n"
    "- Use the user's language if obvious from the message; otherwise use system_profile.user_language_hint.\n"
    "- Maintain a calm, minimal money-tool tone (not chatty).\n"
    '- If the user asks for normal banking actions (send money, buy data, check balance), output handoff="domain" and leave message empty.\n'
    'Output ONLY JSON with keys: handoff, language, message.'
)


def normalize_language_hint(language: str | None) -> str:
    if not language:
        return "en"
    value = language.strip().lower()
    if value in {"en", "english"}:
        return "en"
    if value in {"yo", "yoruba"}:
        return "yo"
    if value in {"ha", "hausa"}:
        return "ha"
    if value in {"pcm", "pidgin", "nigerian pidgin", "naija"}:
        return "pcm"
    return "en"


def fallback_meta_message(profile: SystemProfile) -> str:
    supported = ", ".join(profile.supported_domains)
    unsupported = ", ".join(profile.unsupported_capabilities)
    return (
        f"I'm {profile.name}. {profile.description}\n"
        f"I can help with: {supported}.\n"
        f"I can't do: {unsupported}.\n"
        "What would you like to do?"
    )


def enforce_capability_contract(message: str, profile: SystemProfile) -> str:
    if not message:
        return fallback_meta_message(profile)

    lowered = message.lower()
    neg_markers = (
        "not",
        "can't",
        "cannot",
        "do not",
        "don't",
        "not yet",
        "not available",
        "unsupported",
        "coming soon",
        "not currently",
    )

    for capability in profile.unsupported_capabilities:
        cap_lower = capability.lower()
        if cap_lower in lowered and not any(marker in lowered for marker in neg_markers):
            return fallback_meta_message(profile)

    return message


async def generate_meta_reply(
    llm: Any | None,
    *,
    user_message: str,
    user_language_hint: str | None,
    profile: SystemProfile = SYSTEM_PROFILE,
    active_session: dict[str, Any] | None = None,
) -> tuple[str, Literal["meta", "domain"]]:
    """Generate a meta response grounded in the SystemProfile."""
    if not llm:
        return fallback_meta_message(profile), "meta"

    payload: dict[str, Any] = {
        "user_message": user_message,
        "user_language_hint": normalize_language_hint(user_language_hint),
        "system_profile": asdict(profile),
    }
    if active_session:
        payload["active_session"] = active_session

    try:
        meta_llm = llm.with_structured_output(MetaReply)
        result = await meta_llm.ainvoke(
            [
                {"role": "system", "content": META_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
            ]
        )
        meta_reply = result if isinstance(result, MetaReply) else MetaReply.model_validate(result)
    except Exception:
        return fallback_meta_message(profile), "meta"

    if meta_reply.handoff != "meta":
        return "", meta_reply.handoff

    safe_message = enforce_capability_contract(meta_reply.message, profile)
    return safe_message, "meta"
