"""Conversation responder service for bounded casual replies."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_openai import ChatOpenAI

import apps.chat.src.agent.orchestrator.conversation.conversation_responder_contextual as contextual_responder
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_prompts as responder_prompts
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_text as responder_text
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_unsupported as unsupported_responder
from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import (
    build_conversation_grounding,
    conversation_display_name,
)
from banking.presentation.i18n.locale import LocaleManager
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config

__all__ = ["ConversationResponder"]


class ConversationResponder:
    """Generates bounded conversational replies for casual non-banking turns."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm

    async def generate_reply(
        self,
        text: str,
        user_ctx: dict[str, Any],
        intent: str | None = None,
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
        is_contextual_worker_followup = intent == contextual_responder.CONTEXTUAL_WORKER_FOLLOWUP_INTENT
        is_contextual_meta_followup = intent == contextual_responder.CONTEXTUAL_META_FOLLOWUP_INTENT
        is_unsupported_capability_followup = intent == unsupported_responder.UNSUPPORTED_CAPABILITY_FOLLOWUP_INTENT
        now = datetime.now(ZoneInfo("Africa/Lagos"))
        casual_streak = responder_text.count_trailing_casual_replies(history, locale=locale)
        redirect_text = responder_text.redirect_text(locale, casual_streak=casual_streak)
        prefers_banking_humor = bool(responder_text.JOKE_PATTERN_RE.search(text))
        if (
            not is_contextual_worker_followup
            and not is_contextual_meta_followup
            and not is_unsupported_capability_followup
            and casual_streak >= responder_text.MAX_CASUAL_REPLY_STREAK
        ):
            return redirect_text

        is_joke_turn = responder_text.is_joke_turn(text, history)
        is_banking_reaction = (
            not is_contextual_worker_followup
            and not is_contextual_meta_followup
            and not is_unsupported_capability_followup
            and casual_streak == 0
            and responder_text.is_banking_result_reaction(text, history)
        )
        if is_contextual_worker_followup:
            grounded_reply = contextual_responder.contextual_worker_grounded_reply(text, user_ctx, locale)
            if grounded_reply:
                return grounded_reply

        prompt_input = responder_prompts.ConversationResponderPromptInput(
            text=text,
            user_ctx=user_ctx,
            locale=locale,
            language=language,
            name=name,
            history=history,
            grounding=grounding if isinstance(grounding, dict) else None,
            now=now,
            casual_streak=casual_streak,
            prefers_banking_humor=prefers_banking_humor,
            is_joke_turn=is_joke_turn,
            is_banking_reaction=is_banking_reaction,
            is_contextual_worker_followup=is_contextual_worker_followup,
            is_contextual_meta_followup=is_contextual_meta_followup,
            is_unsupported_capability_followup=is_unsupported_capability_followup,
        )

        reply = await ainvoke_with_config(
            self.llm,
            responder_prompts.build_conversation_responder_messages(prompt_input),
            config=build_llm_runnable_config(
                role="conversation_responder",
                phone_number=str(user_ctx.get("phone_number") or ""),
                locale=locale,
                task_domain="conversation",
                extra_metadata={
                    "intent": intent,
                    "casual_streak": casual_streak,
                    "contextual_worker_followup": is_contextual_worker_followup,
                    "unsupported_capability_followup": is_unsupported_capability_followup,
                },
            )
            or None,
        )

        raw_content: str | None
        if isinstance(reply, str):
            raw_content = reply
        else:
            response_content: Any = getattr(reply, "content", None)
            raw_content = response_content if isinstance(response_content, str) else None

        preface = responder_text.sanitize_preface(raw_content, locale=locale)
        if not preface:
            if is_contextual_worker_followup:
                return contextual_responder.contextual_worker_fallback_reply(text, user_ctx, locale=locale)
            if is_contextual_meta_followup:
                return contextual_responder.contextual_meta_fallback_reply(user_ctx, locale=locale)
            if is_unsupported_capability_followup:
                return unsupported_responder.unsupported_capability_fallback_reply(user_ctx, locale)
            if is_joke_turn:
                joke = responder_text.deterministic_joke_fallback(casual_streak=casual_streak)
                return f"{joke}\n{redirect_text}"
            return redirect_text
        if is_contextual_worker_followup:
            if contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(
                preface
            ) or contextual_responder.CONTEXTUAL_UNGROUNDED_PREFACE_RE.search(preface):
                return contextual_responder.contextual_worker_fallback_reply(text, user_ctx, locale=locale)
            return preface
        if is_contextual_meta_followup:
            if contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(
                preface
            ) or responder_text.is_banking_refusal_reply(preface, locale=locale):
                return contextual_responder.contextual_meta_fallback_reply(user_ctx, locale=locale)
            return preface
        if is_unsupported_capability_followup:
            if contextual_responder.CONTEXTUAL_ACTION_PROMISE_RE.search(
                preface
            ) or unsupported_responder.UNSUPPORTED_CAPABILITY_PROMISE_RE.search(preface):
                return unsupported_responder.unsupported_capability_fallback_reply(user_ctx, locale)
            return preface
        if is_banking_reaction:
            return preface
        if preface == redirect_text:
            return redirect_text
        return f"{preface}\n{redirect_text}"
