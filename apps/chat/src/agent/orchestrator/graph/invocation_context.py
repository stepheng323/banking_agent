"""Invocation input and context hydration helpers for the orchestrator graph."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import (
    attach_conversation_grounding,
    conversation_topic_for_response,
)
from apps.chat.src.agent.orchestrator.graph.preflight import InvocationPreflight
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.presentation.intents import map_outbox_to_intents
from shared.config.settings import settings
from shared.i18n.locale import LocaleManager


def build_graph_inputs(context: MessageContext) -> dict[str, Any]:
    return {
        "user_id": context.phone_number,
        "phone_number": context.phone_number,
        "last_message_text": context.text,
        "last_message_id": context.message_id,
        "last_callback": None,
        "has_quote": bool(context.quoted_message_id),
        "quoted_message_id": context.quoted_message_id,
        "channel": context.channel,
        "channel_identity": context.channel_identity,
    }


async def load_invocation_context(
    *,
    context_manager: Any,
    context: MessageContext,
    preflight: InvocationPreflight,
    path_label: str,
) -> dict[str, Any]:
    user_ctx, _, _, _ = await context_manager.load_context_parallel(
        context.phone_number,
        path_label=path_label,
        user=context.resolved_user,
        profile_mode=preflight.profile_mode,
        account_mode=preflight.account_mode,
        beneficiary_mode=preflight.beneficiary_mode,
    )
    loaded_context: dict[str, Any] = {
        "profile": user_ctx.get("profile"),
        "accounts": user_ctx.get("accounts"),
        "beneficiaries": user_ctx.get("beneficiaries"),
        "history": user_ctx.get("history", []),
        "channel_metadata": dict(context.channel_metadata or {}),
        "language": LocaleManager.normalize(user_ctx.get("language")).value,
        "detected_language": LocaleManager.normalize(user_ctx.get("language")).value,
        "user_id": user_ctx.get("profile", {}).get("id") if user_ctx.get("profile") else None,
        "account_context_mode": preflight.account_mode,
        "beneficiary_context_mode": preflight.beneficiary_mode,
    }
    return attach_conversation_grounding(loaded_context)


def typing_visibility_delay_ms(channel: str) -> int:
    if channel == "whatsapp":
        return settings.whatsapp.typing_indicator_delay_ms
    if channel == "telegram":
        return settings.telegram_typing_indicator_delay_ms
    return 0


def build_invocation_result(
    *,
    final_state: dict[str, Any],
    loaded_context: dict[str, Any],
    semantic_path_shape: str,
) -> dict[str, Any]:
    outbox = final_state.get("outbox", [])
    response_text = final_state.get("final_response")
    resolved_locale = LocaleManager.normalize(
        (final_state.get("loaded_context") or {}).get("language") or loaded_context.get("language")
    ).value
    return {
        "text": response_text,
        "intents": map_outbox_to_intents(outbox, response_text),
        "outbox": outbox,
        "locale": resolved_locale,
        "delivery_metadata": {},
        "semantic_path_shape": semantic_path_shape,
        "conversation_topic": final_state.get("conversation_topic")
        or conversation_topic_for_response(
            response_text,
            semantic_path_shape=semantic_path_shape,
            routing_decision=final_state.get("routing_decision"),
        ),
        "suppress_empty_fallback": bool(final_state.get("suppress_empty_fallback")),
    }


__all__ = [
    "build_graph_inputs",
    "build_invocation_result",
    "load_invocation_context",
    "typing_visibility_delay_ms",
]
