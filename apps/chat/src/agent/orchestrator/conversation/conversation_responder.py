"""Conversation responder service for bounded casual replies."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI

import apps.chat.src.agent.orchestrator.conversation.conversation_responder_contextual as contextual_responder
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_prompts as responder_prompts
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_text as responder_text
from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import (
    build_conversation_grounding,
    conversation_display_name,
)
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RESPONSE_KEY_CTX,
    ConversationResponseMode,
)
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_validation import validate_and_fallback
from banking.policy.service import resolve_available_conversational_suggestions
from banking.presentation.i18n.locale import LocaleManager
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import estimated_tokens_from_chars, record_llm_call
from shared.observability.llm_http import (
    start_llm_http_recording,
    stop_llm_http_recording,
    summarize_llm_http_records,
)
from shared.observability.llm_provider_metadata import extract_provider_llm_metadata
from shared.utils.logging import get_logger

__all__ = ["ConversationResponder"]

logger = get_logger(__name__)


class ConversationResponder:
    """Generates bounded conversational replies for casual non-banking turns."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm

    async def generate_reply(
        self,
        text: str,
        user_ctx: dict[str, Any],
        mode: ConversationResponseMode,
    ) -> str:
        """Generate a short safe reply with a deterministic redirect when needed."""
        profile = user_ctx.get("profile") or {}
        grounding = user_ctx.get("conversation_grounding")
        if not isinstance(grounding, dict):
            grounding = build_conversation_grounding(user_ctx)
        name = conversation_display_name(user_ctx) or (
            profile.get("full_name") or profile.get("first_name") if isinstance(profile, dict) else None
        )

        locale = LocaleManager.normalize(user_ctx.get("language")).value
        language = responder_text.locale_to_language_label(locale)
        history = user_ctx.get("history") or []
        now = datetime.now(ZoneInfo("Africa/Lagos"))
        casual_streak = responder_text.count_trailing_casual_replies(history, locale=locale)
        prefers_banking_humor = bool(responder_text.JOKE_PATTERN_RE.search(text))

        if mode == ConversationResponseMode.CASUAL and casual_streak >= responder_text.MAX_CASUAL_REPLY_STREAK:
            logger.info(
                "conversation_responder_casual_limit",
                mode=mode.value,
                locale=locale,
                casual_streak=casual_streak,
            )
            return responder_text.redirect_text(locale, casual_streak=casual_streak)

        # Determine allowed suggestions from live capability policy
        allowed_suggestions = resolve_available_conversational_suggestions(locale=locale)

        is_joke_turn = responder_text.is_joke_turn(text, history)
        is_banking_reaction = (
            mode
            not in (
                ConversationResponseMode.CONTEXTUAL_WORKER,
                ConversationResponseMode.CONTEXTUAL_META,
                ConversationResponseMode.UNSUPPORTED_BOUNDARY,
                ConversationResponseMode.SOCIAL_META,
            )
            and casual_streak == 0
            and responder_text.is_banking_result_reaction(text, history)
        )

        if mode == ConversationResponseMode.CONTEXTUAL_WORKER:
            grounded_reply = contextual_responder.contextual_worker_grounded_reply(text, user_ctx, locale)
            if grounded_reply:
                return grounded_reply

        prompt_grounding: dict[str, Any] | None = grounding if isinstance(grounding, dict) else None
        if mode == ConversationResponseMode.SOCIAL_META and user_ctx.get(SOCIAL_META_RESPONSE_KEY_CTX) in {
            "conversational.greeting",
            "conversational.greeting_named",
            "conversational.checkin",
        }:
            prompt_grounding = None
            logger.info("conversation_social_context_reset", reason="fresh_social_opener")

        prompt_input = responder_prompts.ConversationResponderPromptInput(
            text=text,
            user_ctx=user_ctx,
            locale=locale,
            language=language,
            name=name,
            history=history,
            grounding=prompt_grounding,
            now=now,
            casual_streak=casual_streak,
            prefers_banking_humor=prefers_banking_humor,
            is_joke_turn=is_joke_turn,
            is_banking_reaction=is_banking_reaction,
            mode=mode,
            allowed_suggestions=allowed_suggestions,
        )

        messages = responder_prompts.build_conversation_responder_messages(prompt_input)
        config = (
            build_llm_runnable_config(
                role="conversation_responder",
                phone_number=str(user_ctx.get("phone_number") or ""),
                locale=locale,
                task_domain="conversation",
                extra_metadata={
                    "mode": mode.value,
                    "prompt_profile": mode.value,
                    "prompt_cache_key_version": "none",
                    "casual_streak": casual_streak,
                    "allowed_suggestion_count": len(allowed_suggestions),
                },
            )
            or None
        )
        system_chars, user_chars = _message_char_counts(messages)
        start = time.perf_counter()
        http_recording_token = start_llm_http_recording()
        try:
            reply = await ainvoke_with_config(
                self.llm,
                messages,
                config=config,
                invocation_kwargs={"temperature": 0.3},
                role="conversation_responder",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))
            record_llm_call(
                event_name="conversation_responder_llm_call",
                duration_ms=duration_ms,
                model=_model_name(self.llm),
                response_type="ConversationResponderReply",
                system_chars=system_chars,
                user_chars=user_chars,
                latency_span="conversation_responder_llm",
                output_json_chars=0,
                output_token_estimate=0,
                extra_fields={
                    **http_metrics,
                    "mode": mode.value,
                    "prompt_profile": mode.value,
                    "prompt_cache_key_version": "none",
                    "locale": locale,
                    "casual_streak": casual_streak,
                    "allowed_suggestion_count": len(allowed_suggestions),
                },
                error_type=type(exc).__name__,
            )
            logger.warning(
                "conversation_responder_generation_fallback",
                mode=mode.value,
                locale=locale,
                error_type=type(exc).__name__,
                allowed_suggestion_count=len(allowed_suggestions),
            )
            return validate_and_fallback(
                raw_content=None,
                mode=mode,
                text=text,
                user_ctx=user_ctx,
                locale=locale,
                allowed_suggestions=allowed_suggestions,
                is_banking_reaction=is_banking_reaction,
                is_joke_turn=is_joke_turn,
            )
        http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))

        raw_content: str | None
        if isinstance(reply, str):
            raw_content = reply
        else:
            response_content: Any = getattr(reply, "content", None)
            raw_content = response_content if isinstance(response_content, str) else None
        output_chars = len(raw_content or "")
        provider_fields = {**http_metrics, **extract_provider_llm_metadata(reply)}
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "conversation_responder_llm_call",
            duration_ms=round(duration_ms, 2),
            model=_model_name(self.llm),
            system_chars=system_chars,
            user_chars=user_chars,
            output_json_chars=output_chars,
            output_token_estimate=estimated_tokens_from_chars(output_chars),
            intent=mode.value,
            locale=locale,
            **provider_fields,
        )
        record_llm_call(
            event_name="conversation_responder_llm_call",
            duration_ms=duration_ms,
            model=_model_name(self.llm),
            response_type="ConversationResponderReply",
            system_chars=system_chars,
            user_chars=user_chars,
            latency_span="conversation_responder_llm",
            output_json_chars=output_chars,
            output_token_estimate=estimated_tokens_from_chars(output_chars),
            extra_fields={
                **provider_fields,
                "mode": mode.value,
                "prompt_profile": mode.value,
                "prompt_cache_key_version": "none",
                "locale": locale,
                "casual_streak": casual_streak,
                "allowed_suggestion_count": len(allowed_suggestions),
            },
        )

        return validate_and_fallback(
            raw_content=raw_content,
            mode=mode,
            text=text,
            user_ctx=user_ctx,
            locale=locale,
            allowed_suggestions=allowed_suggestions,
            is_banking_reaction=is_banking_reaction,
            is_joke_turn=is_joke_turn,
        )


def _model_name(llm: Any) -> str | None:
    return getattr(llm, "model_name", None) or getattr(llm, "model", None)


def _message_char_counts(messages: list[dict[str, str]]) -> tuple[int, int]:
    system_chars = 0
    user_chars = 0
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "system":
            system_chars += len(content)
        else:
            user_chars += len(content)
    return system_chars, user_chars
