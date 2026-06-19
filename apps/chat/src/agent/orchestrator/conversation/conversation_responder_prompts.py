"""Prompt assembly for bounded conversational replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry as unsupported_registry
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_contextual as contextual_responder
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_intents as responder_intents
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_text as responder_text
from apps.chat.src.agent.assistant_profile.voice import build_conversation_voice_block


@dataclass(frozen=True, slots=True)
class ConversationResponderPromptInput:
    text: str
    user_ctx: dict[str, Any]
    locale: str
    language: str
    name: str | None
    history: list[Any]
    grounding: dict[str, Any] | None
    now: datetime
    casual_streak: int
    prefers_banking_humor: bool
    is_joke_turn: bool
    is_banking_reaction: bool
    is_social_meta: bool
    is_contextual_worker_followup: bool
    is_contextual_meta_followup: bool
    is_unsupported_capability_followup: bool


def build_conversation_responder_messages(
    prompt_input: ConversationResponderPromptInput,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _build_system_prompt(prompt_input)},
        {"role": "user", "content": _build_user_prompt(prompt_input)},
    ]


def _build_system_prompt(prompt_input: ConversationResponderPromptInput) -> str:
    system = build_conversation_voice_block(locale=prompt_input.language, channel="WhatsApp")
    system += f"Reply in {prompt_input.language}.\n"

    if prompt_input.is_contextual_worker_followup:
        system += (
            "The user's message is an acknowledgement or commentary after a banking assistant result.\n"
            "Write ONLY a short grounded acknowledgement.\n"
            "Rules:\n"
            "- Use only the recent context provided.\n"
            "- Keep it to 1 short sentence.\n"
            "- Do not ask for a transaction reference.\n"
            "- Do not start a support, query, transfer, airtime, data, account, or FAQ workflow.\n"
            "- Do not offer to retry, send money, buy anything, create tickets, refund, or reverse anything.\n"
            "- No generic banking redirect.\n"
            "- No markdown, no emojis.\n"
            "- If no specific grounded acknowledgement is possible, return an empty string.\n"
        )
    elif prompt_input.is_social_meta:
        system += (
            "The user's message is a social opener, check-in, or thanks for the banking assistant.\n"
            "Write ONLY the final short reply. Do not add a separate redirect line.\n"
            "Rules:\n"
            "- Match the user's energy, vibe, and tone lightly while staying professional.\n"
            "- Keep it to 1 or 2 short sentences.\n"
            "- For greetings and check-ins: if the conversation history is empty or we haven't offered help "
            "yet, include a natural banking anchor (offer help with transfers, airtime/data, balances, or "
            "transaction queries). If the history shows we already introduced these capabilities, do NOT repeat "
            "the full list of services; instead, use a brief banking constraint (e.g. 'how can I help with your "
            "banking today?' or 'what banking task can we do next?') to keep the conversation scoped to banking "
            "without being repetitive.\n"
            "- For thanks, acknowledge briefly and invite the next banking task if it feels natural.\n"
            "- Do not start a support, query, transfer, airtime, data, account, or FAQ workflow.\n"
            "- Do not offer to retry, send money, buy anything, create tickets, refund, or reverse anything.\n"
            "- No financial, legal, medical, tax, or investment advice.\n"
            "- No promises about unsupported capabilities.\n"
            "- No markdown, no emojis.\n"
            "- If the user's message is unsafe or not a social/meta turn, return an empty string.\n"
        )
    elif prompt_input.is_contextual_meta_followup:
        system += (
            "The user is reacting to the assistant's previous brand/product explanation.\n"
            "Write ONLY a short grounded acknowledgement.\n"
            "Rules:\n"
            "- Use only the recent context provided.\n"
            "- Keep it to 1 short sentence.\n"
            "- If the last topic is brand_origin, briefly connect flow/liquidity/control.\n"
            "- If the last topic is product_identity, briefly connect to moving/checking money clearly.\n"
            "- Do not start a support, query, transfer, airtime, data, account, or FAQ workflow.\n"
            "- Do not offer to retry, send money, buy anything, create tickets, refund, or reverse anything.\n"
            "- No generic banking redirect.\n"
            "- No markdown, no emojis.\n"
            "- If no specific grounded acknowledgement is possible, return an empty string.\n"
        )
    elif prompt_input.is_unsupported_capability_followup:
        system += _unsupported_capability_system_rules(prompt_input)
    else:
        system += (
            "The user's message is non-banking or casual chat.\n"
            "Write ONLY a short conversational preface, not the banking redirect.\n"
            "Rules:\n"
            "- Answer briefly and harmlessly.\n"
            "- Keep it to 1 or 2 short sentences.\n"
            "- For harmless casual asks like jokes, tiny banter, or date/time, answer directly "
            "instead of refusing.\n"
            "- If the user asks for a joke or playful banter, prefer banking-, money-, balance-, savings-, "
            "or transfer-themed humor.\n"
            "- No financial, legal, medical, tax, or investment advice.\n"
            "- No promises about unsupported capabilities.\n"
            "- No broad topic drift, no markdown, no emojis.\n"
            "- If asked about the current date or time, use the runtime Lagos timestamp provided.\n"
            "- Do not say you only handle banking or that you cannot help with harmless casual chat.\n"
            "- If the ask is unsafe, too broad, or not suitable, return an empty string.\n"
        )
        if prompt_input.is_banking_reaction:
            system += (
                "- The user is reacting to recent banking information. Give only the short empathetic "
                "reply; no generic banking redirect.\n"
            )

    if (
        not prompt_input.is_contextual_worker_followup
        and not prompt_input.is_contextual_meta_followup
        and not prompt_input.is_unsupported_capability_followup
        and not prompt_input.is_social_meta
        and prompt_input.casual_streak >= 2
    ):
        system += "- The user has stayed in casual-chat mode for several turns, so keep the reply extra short.\n"

    return system


def _unsupported_capability_system_rules(prompt_input: ConversationResponderPromptInput) -> str:
    unsupported = prompt_input.user_ctx.get("unsupported_capability")
    if isinstance(unsupported, dict):
        capability = unsupported.get("label") or unsupported.get("capability")
        supported = unsupported.get("supported_alternatives") or unsupported.get("supported")
        safety_note = unsupported.get("safety_note")
    else:
        capability = None
        supported = None
        safety_note = None

    capability = str(capability or "that unsupported capability")
    supported = str(supported or unsupported_registry.localized_supported_alternatives(prompt_input.locale))
    system = (
        f"The user is continuing to ask for an unsupported capability: {capability}.\n"
        "Write ONLY a short bounded reply.\n"
        "Rules:\n"
        "- Keep it to 1 or 2 short sentences.\n"
        "- Acknowledge briefly, but do not negotiate or keep the topic open.\n"
        f"- Say the assistant cannot help with {capability}.\n"
        f"- Redirect to supported tasks: {supported}.\n"
        "- Do not mention or use stale transfer, recipient, amount, account, or transaction context.\n"
        "- Do not start a support, query, transfer, airtime, data, account, or FAQ workflow.\n"
        "- Do not provide financial advice, trading/investment/crypto recommendations, lender suggestions, "
        "loan approvals, international transfer execution, export downloads, or unsupported history "
        "retrieval.\n"
        "- No markdown, no emojis.\n"
        "- If the reply would promise or enable the unsupported capability, return an empty string.\n"
    )
    if safety_note:
        system += f"- {safety_note}\n"
    return system


def _build_user_prompt(prompt_input: ConversationResponderPromptInput) -> str:
    user_parts = [
        f"Runtime Lagos timestamp: {prompt_input.now.strftime('%A, %B %d, %Y %H:%M %Z')}",
        f"User message: {prompt_input.text.strip()}",
        f"Recent casual streak: {prompt_input.casual_streak}",
    ]
    if prompt_input.grounding:
        topic = prompt_input.grounding.get("last_topic")
        if topic:
            user_parts.append(f"Conversation last topic: {topic}")
        last_assistant_message = prompt_input.grounding.get("last_assistant_message")
        if isinstance(last_assistant_message, str) and last_assistant_message.strip():
            user_parts.append(f"Last assistant message: {last_assistant_message.strip()}")
        if grounding_turn_lines := _grounding_turn_lines(prompt_input.grounding):
            user_parts.append("Safe recent turns:\n" + "\n".join(grounding_turn_lines))

    if prompt_input.is_social_meta:
        response_key = prompt_input.user_ctx.get(responder_intents.SOCIAL_META_RESPONSE_KEY_CTX)
        if response_key:
            user_parts.append(f"Social response key: {response_key}")
        render_params = prompt_input.user_ctx.get(responder_intents.SOCIAL_META_RENDER_PARAMS_CTX)
        if isinstance(render_params, dict):
            display_name = render_params.get("display_name")
            if display_name:
                user_parts.append(f"Suggested display name: {display_name}")

    contextual_summary = prompt_input.user_ctx.get(contextual_responder.CONTEXTUAL_WORKER_FOLLOWUP_INTENT)
    if prompt_input.is_contextual_worker_followup and contextual_summary:
        user_parts.append(f"Recent banking context: {contextual_summary}")

    if prompt_input.is_unsupported_capability_followup:
        _append_unsupported_capability_user_parts(user_parts, prompt_input.user_ctx)

    if (
        not prompt_input.is_contextual_worker_followup
        and not prompt_input.is_contextual_meta_followup
        and not prompt_input.is_unsupported_capability_followup
        and (prompt_input.prefers_banking_humor or prompt_input.is_joke_turn)
    ):
        user_parts.append("Use a banking-related joke or money-themed playful line if you answer with humor.")
    if prompt_input.name:
        user_parts.append(f"User name: {prompt_input.name}")

    history_text = responder_text.recent_history_text(prompt_input.history)
    if history_text:
        user_parts.append(f"\nRecent turns:\n{history_text}")

    return "\n".join(user_parts)


def _grounding_turn_lines(grounding: dict[str, Any]) -> list[str]:
    grounding_turns = grounding.get("recent_turns")
    grounding_turn_lines: list[str] = []
    if not isinstance(grounding_turns, list):
        return grounding_turn_lines

    for turn in grounding_turns[-4:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower() or "user"
        content = str(turn.get("content") or "").strip()
        if content:
            grounding_turn_lines.append(f"{role}: {content}")
    return grounding_turn_lines


def _append_unsupported_capability_user_parts(user_parts: list[str], user_ctx: dict[str, Any]) -> None:
    unsupported = user_ctx.get("unsupported_capability")
    if not isinstance(unsupported, dict):
        return
    capability_label = unsupported.get("label") or unsupported.get("capability") or "unknown"
    user_parts.append(f"Unsupported capability: {capability_label}")
    capability_key = unsupported.get("key")
    if capability_key:
        user_parts.append(f"Unsupported capability key: {capability_key}")
    user_parts.append(f"Unsupported follow-up count: {unsupported.get('followup_count') or 0}")
    alternatives = unsupported.get("supported_alternatives") or unsupported.get("supported")
    if alternatives:
        user_parts.append(f"Supported alternatives: {alternatives}")


__all__ = [
    "ConversationResponderPromptInput",
    "build_conversation_responder_messages",
]
