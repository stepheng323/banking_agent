"""Fail-closed validation and localized fallbacks for generated conversation replies."""

from __future__ import annotations

import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.conversation import conversation_responder_contextual as contextual_responder
from apps.chat.src.agent.orchestrator.conversation import conversation_responder_text as responder_text
from apps.chat.src.agent.orchestrator.conversation import conversation_responder_unsupported as unsupported_responder
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    ConversationResponseMode,
)
from banking.policy.models import AvailableConversationalSuggestion
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import message_key_exists, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

EXECUTION_PROMISE_RE = re.compile(
    r"\b(?:i will|i'll|let me|we will|we'll)\s+"
    r"(?:send|transfer|process|execute|retry|resend|buy|purchase|create|submit|reverse|refund)\b|"
    r"\b(?:transferring|processing|executing|i have sent|i've sent)\b",
    re.IGNORECASE,
)
FINANCIAL_ADVICE_RE = re.compile(
    r"\b(?:you should invest|i recommend buying|buy this stock|here is investment advice|"
    r"this is a good investment|tax advice|legal advice)\b",
    re.IGNORECASE,
)


def validate_and_fallback(
    raw_content: str | None,
    mode: ConversationResponseMode,
    text: str,
    user_ctx: dict[str, Any],
    locale: str,
    allowed_suggestions: list[AvailableConversationalSuggestion] | None = None,
    is_banking_reaction: bool = False,
    is_joke_turn: bool = False,
) -> str:
    """Return a bounded generated response or the deterministic fallback for its mode."""
    content = (raw_content or "").strip()
    fallback_reason: str | None = None

    if not content:
        fallback_reason = "empty"
    elif len(content) > 280:
        fallback_reason = "character_limit"
    elif len(content.splitlines()) > 3:
        fallback_reason = "line_limit"
    elif len(re.findall(r"[.!?]+(?:\s|$)", content)) > 2:
        fallback_reason = "sentence_limit"
    elif EXECUTION_PROMISE_RE.search(content):
        fallback_reason = "execution_claim"
    elif FINANCIAL_ADVICE_RE.search(content):
        fallback_reason = "advice_claim"
    elif mode == ConversationResponseMode.CONTEXTUAL_WORKER and (
        contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(content)
        or contextual_responder.CONTEXTUAL_UNGROUNDED_PREFACE_RE.search(content)
    ):
        fallback_reason = "contextual_worker_ungrounded"
    elif mode == ConversationResponseMode.CONTEXTUAL_META and (
        contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(content)
        or responder_text.is_banking_refusal_reply(content, locale=locale)
    ):
        fallback_reason = "contextual_meta_invalid"
    elif mode == ConversationResponseMode.UNSUPPORTED_BOUNDARY and (
        contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(content)
        or unsupported_responder.UNSUPPORTED_CAPABILITY_PROMISE_RE.search(content)
    ):
        fallback_reason = "unsupported_promise"
    elif mode == ConversationResponseMode.SOCIAL_META:
        if contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(content):
            fallback_reason = "social_action_promise"
        elif responder_text.is_banking_refusal_reply(
            content,
            locale=locale,
            allow_positive_banking_anchor=True,
        ):
            fallback_reason = "social_stale_refusal"

    if fallback_reason is None:
        logger.info(
            "conversation_responder_validation",
            mode=mode.value,
            outcome="accepted",
            allowed_suggestion_count=len(allowed_suggestions or []),
        )
        return content

    logger.info(
        "conversation_responder_validation",
        mode=mode.value,
        outcome="fallback",
        fallback_reason=fallback_reason,
        allowed_suggestion_count=len(allowed_suggestions or []),
    )
    return _fallback_for_mode(
        mode,
        text,
        user_ctx,
        locale,
        allowed_suggestions or [],
        is_banking_reaction,
        is_joke_turn,
    )


def _fallback_for_mode(
    mode: ConversationResponseMode,
    text: str,
    user_ctx: dict[str, Any],
    locale: str,
    allowed_suggestions: list[AvailableConversationalSuggestion],
    is_banking_reaction: bool,
    is_joke_turn: bool,
) -> str:
    if mode == ConversationResponseMode.CONTEXTUAL_WORKER:
        return contextual_responder.contextual_worker_fallback_reply(text, user_ctx, locale=locale)
    if mode == ConversationResponseMode.CONTEXTUAL_META:
        return contextual_responder.contextual_meta_fallback_reply(user_ctx, locale=locale)
    if mode == ConversationResponseMode.UNSUPPORTED_BOUNDARY:
        return unsupported_responder.unsupported_capability_fallback_reply(user_ctx, locale)
    if mode == ConversationResponseMode.SOCIAL_META:
        response_key = str(user_ctx.get(SOCIAL_META_RESPONSE_KEY_CTX) or "conversational.greeting")
        if not message_key_exists(response_key, locale):
            response_key = "conversational.greeting"
        params = user_ctx.get(SOCIAL_META_RENDER_PARAMS_CTX)
        return render_message(
            cast(MessageKey, response_key),
            locale,
            params if isinstance(params, dict) else None,
        )
    if mode == ConversationResponseMode.CAPABILITIES:
        labels = _suggestion_labels(allowed_suggestions, limit=4)
        if labels:
            return render_message(
                "conversational.capabilities_dynamic",
                locale,
                {"capabilities": _natural_list(labels, locale)},
            )
        return render_message("conversational.capability_question", locale)
    if mode == ConversationResponseMode.CLARIFY:
        labels = _suggestion_labels(allowed_suggestions, limit=2)
        if labels:
            return render_message(
                "conversational.clarify_dynamic",
                locale,
                {"suggestions": _natural_list(labels, locale)},
            )
        return render_message("conversational.clarify", locale)
    if mode == ConversationResponseMode.OUT_OF_SCOPE:
        return render_message("conversational.out_of_scope", locale)
    return responder_text.redirect_text(locale, casual_streak=0 if is_joke_turn or is_banking_reaction else 0)


def _suggestion_labels(
    suggestions: list[AvailableConversationalSuggestion],
    *,
    limit: int,
) -> list[str]:
    return [suggestion.label for suggestion in suggestions[:limit] if suggestion.label]


def _natural_list(labels: list[str], locale: str) -> str:
    if len(labels) < 2:
        return labels[0] if labels else ""
    connector = {"ha": "ko", "ig": "ma ọ bụ", "pcm": "or", "yo": "tàbí"}.get(locale, "or")
    return ", ".join(labels[:-1]) + f", {connector} {labels[-1]}"


__all__ = ["validate_and_fallback"]
