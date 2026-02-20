"""Meta reply utilities for identity/capability and conversational responses."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.models.domain import MetaIntent
from apps.core.src.agent.orchestrator.system_profile import SYSTEM_PROFILE, SystemProfile
from shared.policy import build_meta_policy_payload, get_cached_policy


class MetaReply(BaseModel):
    """Output for meta identity/capability replies."""

    handoff: Literal["meta", "domain"] = Field(description="Whether to answer or route to domain")
    language: Literal["en", "yo", "pcm", "ha"] = Field(description="Response language")
    message: str = Field(description="Response message")


META_SYSTEM_PROMPT = (
    "You write short WhatsApp replies for a banking assistant.\n"
    "You MUST follow system_profile and soul_policy exactly.\n"
    "- Never claim a feature that is not listed in system_profile.supported_domains.\n"
    "- If asked about something not supported, say it's not available yet and suggest a supported alternative.\n"
    "- Keep the tone minimal and confident (not chatty).\n"
    "- Keep the reply under 5 lines.\n\n"
    'Return ONLY JSON: {"message":"...", "language":"en|yo|pcm|ha"}'
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
    return f"I'm {profile.name}. {profile.description}\nI can help with: {supported}.\nWhat would you like to do?"


async def generate_meta_reply(
    llm: Any | None,
    *,
    user_message: str,
    user_language_hint: str | None,
    meta_intent: MetaIntent | None = None,
    redis_client: Any | None = None,
    profile: SystemProfile = SYSTEM_PROFILE,
    active_session: dict[str, Any] | None = None,
) -> tuple[str, Literal["meta", "domain"]]:
    """Generate a meta response grounded in the SystemProfile with Caching."""
    policy = get_cached_policy()

    # 1. Deterministic Cache Key Construction
    language = normalize_language_hint(user_language_hint)

    if meta_intent and redis_client:
        cache_key = f"meta:v3:{language}:{meta_intent.value}"
        try:
            cached_msg = await redis_client.get(cache_key)
            if cached_msg:
                # Return cached UTF-8 string
                return cached_msg.decode("utf-8") if isinstance(cached_msg, bytes) else str(cached_msg), "meta"
        except Exception:
            pass  # Fallback to generation on cache error

    if not llm:
        return fallback_meta_message(profile), "meta"

    # 2. LLM Generation (Cache Miss)
    payload: dict[str, Any] = {
        "user_message": user_message,
        "language_hint": language,
        "system_profile": asdict(profile),
        "soul_policy": build_meta_policy_payload(policy),
    }
    if meta_intent:
        payload["intent_context"] = meta_intent.value

    try:
        # Use low temperature for deterministic generation
        meta_llm = llm.with_structured_output(MetaReply).with_config({"configurable": {"temperature": 0.1}})

        result = await meta_llm.ainvoke(
            [
                {"role": "system", "content": META_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
            ]
        )
        meta_reply = result if isinstance(result, MetaReply) else MetaReply.model_validate(result)

        # 3. Cache Write-Back
        if meta_intent and redis_client and meta_reply.handoff == "meta":
            safe_msg = meta_reply.message
            try:
                # Cache for 7 days (static branding answers don't change often)
                await redis_client.setex(cache_key, 604800, safe_msg)
            except Exception:
                pass

        if meta_reply.handoff != "meta":
            return "", meta_reply.handoff

        return meta_reply.message, "meta"

    except Exception:
        return fallback_meta_message(profile), "meta"
