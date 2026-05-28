"""Meta reply utilities for identity/capability and conversational responses."""

from __future__ import annotations

import json
import time
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from apps.chat.src.agent.orchestrator.models.domain import MetaIntent
from shared.assistant_profile.voice import AssistantVoice, get_runtime_voice
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MetaReply(BaseModel):
    """Output for meta identity/capability replies."""

    handoff: Literal["meta", "domain"] = Field(description="Whether to answer or route to domain")
    language: Literal["en", "yo", "pcm", "ha", "ig"] = Field(description="Response language")
    message: str = Field(description="Response message")


META_SYSTEM_PROMPT = (
    "You write short WhatsApp replies for a banking assistant.\n"
    "You MUST follow assistant_voice exactly.\n"
    "- Never claim a feature that is not listed in assistant_voice.supported_domains.\n"
    "- For identity/brand-origin answers, only use facts explicitly present in assistant_voice.\n"
    "- If asked about something not supported, say it's not available yet and suggest a supported alternative.\n"
    "- Follow assistant_voice.tone and assistant_voice.response_rules.\n"
    "- Keep unsupported reroutes consultative and actionable.\n"
    "- Keep the reply under 5 lines.\n\n"
    'Return ONLY JSON: {"message":"...", "language":"en|yo|pcm|ha|ig"}'
)
STRICT_BRAND_TERM_GROUPS = (("lotr", "lord of the rings", "tolkien"),)
STRICT_BRAND_TERMS = ("ring of power", "ring of fire")


def _canonical_allows_brand_term(term: str, canonical: str) -> bool:
    if term in canonical:
        return True
    return any(term in group and any(alias in canonical for alias in group) for group in STRICT_BRAND_TERM_GROUPS)


def normalize_language_hint(language: str | None) -> str:
    return cast(str, LocaleManager.normalize(language).value)


def fallback_meta_message(voice: AssistantVoice, *, locale: str = "en") -> str:
    supported = ", ".join(voice.supported_domains)
    return cast(
        str,
        render_message(
            "meta.fallback",
            locale,
            {"name": voice.name, "description": voice.description, "supported": supported},
            fallback_en=(
                f"I'm {voice.name}. {voice.description}\nI can help with: {supported}.\nWhat would you like to do?"
            ),
        ),
    )


def _grounded_identity_message(voice: AssistantVoice) -> str:
    creator_suffix = f" Built by {voice.creator}." if voice.creator else ""
    return f"I'm {voice.name}. {voice.description}{creator_suffix}"


def _grounded_creator_message(voice: AssistantVoice, *, locale: str) -> str:
    if voice.creator:
        return cast(
            str,
            render_message(
                "meta.creator_fallback",
                locale,
                {"name": voice.name, "creator": voice.creator},
                fallback_en=f"{voice.name} was built by {voice.creator}.",
            ),
        )
    return _grounded_identity_message(voice)


def _grounded_brand_origin_message(voice: AssistantVoice, *, locale: str) -> str:
    if voice.brand_origin:
        return cast(
            str,
            render_message(
                "meta.brand_origin_fallback",
                locale,
                {"brand_origin": voice.brand_origin},
                fallback_en=voice.brand_origin,
            ),
        )
    return cast(str, render_message("conversational.brand_origin", locale))


def _grounded_capabilities_message(voice: AssistantVoice, *, locale: str) -> str:
    supported = ", ".join(voice.supported_domains)
    return cast(
        str,
        render_message(
            "meta.capabilities_fallback",
            locale,
            {"supported": supported},
            fallback_en=f"I can help with: {supported}.",
        ),
    )


def _grounded_limits_message(voice: AssistantVoice, *, locale: str) -> str:
    unsupported = ", ".join(voice.unsupported_capabilities) or "unsupported requests"
    supported = ", ".join(voice.supported_domains)
    return cast(
        str,
        render_message(
            "meta.limits_fallback",
            locale,
            {"unsupported": unsupported, "supported": supported},
            fallback_en=(f"I can't help with {unsupported} here. I can help with {supported}."),
        ),
    )


def _grounded_meta_fallback(voice: AssistantVoice, *, locale: str, meta_intent: MetaIntent | None) -> str:
    if meta_intent == MetaIntent.IDENTITY:
        return _grounded_identity_message(voice)
    if meta_intent == MetaIntent.CREATOR:
        return _grounded_creator_message(voice, locale=locale)
    if meta_intent == MetaIntent.BRAND_ORIGIN:
        return _grounded_brand_origin_message(voice, locale=locale)
    if meta_intent == MetaIntent.CAPABILITIES:
        return _grounded_capabilities_message(voice, locale=locale)
    if meta_intent == MetaIntent.LIMITS:
        return _grounded_limits_message(voice, locale=locale)
    return fallback_meta_message(voice, locale=locale)


def _violates_branding_grounding(message: str, *, voice: AssistantVoice, meta_intent: MetaIntent | None) -> bool:
    if meta_intent not in {
        MetaIntent.IDENTITY,
        MetaIntent.CREATOR,
        MetaIntent.BRAND_ORIGIN,
        MetaIntent.CAPABILITIES,
        MetaIntent.LIMITS,
    }:
        return False
    lowered = message.lower()
    if meta_intent in {MetaIntent.IDENTITY, MetaIntent.CREATOR, MetaIntent.BRAND_ORIGIN}:
        if voice.name.lower() not in lowered:
            return True
    if meta_intent == MetaIntent.CREATOR and voice.creator:
        if voice.creator.lower() not in lowered:
            return True
    if meta_intent == MetaIntent.BRAND_ORIGIN:
        canonical = (voice.brand_origin or "").lower()
        strict_terms = (*STRICT_BRAND_TERMS, *(term for group in STRICT_BRAND_TERM_GROUPS for term in group))
        for term in strict_terms:
            if term in lowered and term not in canonical:
                return not _canonical_allows_brand_term(term, canonical)
    if meta_intent == MetaIntent.CAPABILITIES:
        return any(item.lower() in lowered for item in voice.unsupported_capabilities)
    return False


async def generate_meta_reply(
    llm: Any | None,
    *,
    user_message: str,
    user_language_hint: str | None,
    meta_intent: MetaIntent | None = None,
    redis_client: Any | None = None,
    voice: AssistantVoice | None = None,
    path_label: str = "planner_path",
) -> tuple[str, Literal["meta", "domain"]]:
    """Generate a meta response grounded in AssistantProfile voice with Caching."""
    runtime_voice = voice or get_runtime_voice()

    # 1. Deterministic Cache Key Construction
    language = normalize_language_hint(user_language_hint)

    cache_key: str | None = None
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
        return _grounded_meta_fallback(runtime_voice, locale=language, meta_intent=meta_intent), "meta"

    # 2. LLM Generation (Cache Miss)
    payload: dict[str, Any] = {
        "user_message": user_message,
        "language_hint": language,
        "assistant_voice": runtime_voice.as_meta_payload(),
        "assistant_profile": runtime_voice.as_meta_payload(),
    }
    if meta_intent:
        payload["intent_context"] = meta_intent.value

    try:
        # Use low temperature for deterministic generation
        meta_llm = llm.with_structured_output(MetaReply).with_config({"configurable": {"temperature": 0.1}})

        async def _invoke_meta() -> MetaReply:
            start = time.perf_counter()
            result = await meta_llm.ainvoke(
                [
                    {"role": "system", "content": META_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                ]
            )
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "perf_timer_latency",
                gate="meta_reply_llm",
                span="meta_reply_llm",
                duration_ms=round(duration_ms, 2),
                path_label=path_label,
            )
            return result if isinstance(result, MetaReply) else MetaReply.model_validate(result)

        meta_reply = await _invoke_meta()
        resolved_reply_language = normalize_language_hint(meta_reply.language)
        if resolved_reply_language != language:
            logger.info(
                "meta_reply_language_mismatch_fallback",
                expected=language,
                got=resolved_reply_language,
            )
            return _grounded_meta_fallback(runtime_voice, locale=language, meta_intent=meta_intent), "meta"

        if meta_reply.handoff != "meta":
            return "", meta_reply.handoff

        safe_msg = meta_reply.message
        if _violates_branding_grounding(safe_msg, voice=runtime_voice, meta_intent=meta_intent):
            safe_msg = _grounded_meta_fallback(runtime_voice, locale=language, meta_intent=meta_intent)
            logger.info(
                "meta_reply_grounding_fallback",
                intent=getattr(meta_intent, "value", None),
                reason="grounding_violation",
            )

        # 3. Cache Write-Back
        if cache_key and redis_client:
            try:
                # Cache for 7 days (static branding answers don't change often)
                await redis_client.setex(cache_key, 604800, safe_msg)
            except Exception:
                pass

        return safe_msg, "meta"

    except Exception:
        return _grounded_meta_fallback(runtime_voice, locale=language, meta_intent=meta_intent), "meta"
