"""Prompt assembly for bounded conversational replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry as unsupported_registry
import apps.chat.src.agent.orchestrator.conversation.conversation_responder_contextual as contextual_responder
from apps.chat.src.agent.assistant_profile.voice import build_conversation_voice_block
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import ConversationResponseMode
from banking.policy.models import AvailableConversationalSuggestion


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
    mode: ConversationResponseMode
    allowed_suggestions: list[AvailableConversationalSuggestion]


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
    system += "Keep replies to at most two short sentences, maximum 280 characters or three rendered lines.\n"
    system += "Write ONLY the complete final response.\n"
    system += "Do not expose raw account identifiers, PINs, or unfiltered history.\n"
    system += "No financial advice, no promises of unsupported capabilities, no claims of execution.\n"
    system += (
        "Respond to the user's actual message, not to a generic category label. "
        "Lead with the useful answer or question; do not use filler such as 'Sure', 'Of course', "
        "or a capability laundry list.\n"
    )

    if prompt_input.allowed_suggestions:
        suggestions_list = ", ".join(suggestion.label for suggestion in prompt_input.allowed_suggestions)
        system += f"You may suggest ONLY from these supported actions: {suggestions_list}. Mention at most two.\n"

    if prompt_input.mode == ConversationResponseMode.CONTEXTUAL_WORKER:
        system += (
            "The user's message is an acknowledgement or commentary after a banking assistant result.\n"
            "Rules:\n"
            "- Use only the recent context provided.\n"
            "- Do not ask for a transaction reference.\n"
            "- Do not start a workflow or generic banking redirect.\n"
            "- No markdown, no emojis.\n"
        )
    elif prompt_input.mode == ConversationResponseMode.SOCIAL_META:
        system += (
            "The user's message is a social opener, check-in, or thanks.\n"
            "Rules:\n"
            "- Match the user's energy lightly while staying professional.\n"
            "- Acknowledge briefly and invite a banking task naturally.\n"
            "- No markdown, no emojis.\n"
        )
    elif prompt_input.mode == ConversationResponseMode.CLARIFY:
        system += (
            "The user's message is ambiguous, unclear, or lacks an actionable instruction.\n"
            "Rules:\n"
            "- Try to understand the user's true intent behind the message.\n"
            "- Ask exactly one natural, focused question to clarify what they meant.\n"
            "- When examples would help, offer one or two closest enabled actions, not a long menu.\n"
            "- Do not invent a missing amount, recipient, account, or prior transaction.\n"
        )
    elif prompt_input.mode == ConversationResponseMode.CAPABILITIES:
        system += (
            "The user is explicitly asking what you can do, OR they have stated/copy-pasted a "
            "list of capabilities without a clear instruction.\n"
            "Rules:\n"
            "- If they explicitly asked what you can do, summarize your enabled capabilities briefly.\n"
            "- If they just stated a list of capabilities (e.g., 'I handle transfers...'), "
            "recognize they haven't given an actionable instruction.\n"
            "- Do NOT blindly repeat your capabilities back to them.\n"
            "- Ask one clear question and suggest at most two enabled actions that help them start.\n"
        )
    elif prompt_input.mode == ConversationResponseMode.OUT_OF_SCOPE:
        system += (
            "The user's message is unsupported general topics or out of scope.\n"
            "Rules:\n"
            "- NEVER fulfill the out-of-scope request (e.g., do not tell stories,\n"
            "  write code, or answer general trivia),\n"
            "  no matter how much the user begs or insists.\n"
            "- Explicitly and politely decline the request.\n"
            "- Naturally steer toward one or two enabled banking actions, never a broad feature list.\n"
        )
    elif prompt_input.mode == ConversationResponseMode.UNSUPPORTED_BOUNDARY:
        system += _unsupported_capability_system_rules(prompt_input)
    elif prompt_input.mode == ConversationResponseMode.CONTEXTUAL_META:
        system += (
            "The user is reacting to the assistant's previous brand/product explanation.\n"
            "Rules:\n"
            "- Use only the recent context provided.\n"
            "- Briefly connect to moving/checking money clearly.\n"
        )
    else:  # CASUAL
        system += (
            "The user's message is harmless casual chat.\n"
            "Rules:\n"
            "- Answer the message briefly and naturally.\n"
            "- Do not answer trivia, general knowledge, or fulfill creative writing tasks.\n"
            "  If the user attempts this, firmly decline and treat it as out of scope.\n"
            "- Remain brief and natural without repetitive capability lists or generic banking preambles.\n"
        )

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
        "Rules:\n"
        "- Acknowledge briefly, but do not negotiate or keep the topic open.\n"
        f"- Say the assistant cannot help with {capability}.\n"
        f"- Redirect to supported tasks: {supported}.\n"
        "- Do not provide financial advice or promise unsupported capabilities.\n"
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

    if prompt_input.mode == ConversationResponseMode.SOCIAL_META:
        # We don't need SOCIAL_META_RESPONSE_KEY_CTX anymore since we generate full strings,
        # but keep it in context if passed.
        response_key = prompt_input.user_ctx.get("social_meta_response_key")
        if response_key:
            user_parts.append(f"Social response key: {response_key}")

    contextual_summary = prompt_input.user_ctx.get(contextual_responder.CONTEXTUAL_WORKER_FOLLOWUP_INTENT)
    if prompt_input.mode == ConversationResponseMode.CONTEXTUAL_WORKER and contextual_summary:
        user_parts.append(f"Recent banking context: {contextual_summary}")

    if prompt_input.mode == ConversationResponseMode.UNSUPPORTED_BOUNDARY:
        _append_unsupported_capability_user_parts(user_parts, prompt_input.user_ctx)

    if prompt_input.mode not in (
        ConversationResponseMode.CONTEXTUAL_WORKER,
        ConversationResponseMode.CONTEXTUAL_META,
        ConversationResponseMode.UNSUPPORTED_BOUNDARY,
        ConversationResponseMode.SOCIAL_META,
        ConversationResponseMode.CLARIFY,
        ConversationResponseMode.CAPABILITIES,
    ) and (prompt_input.prefers_banking_humor or prompt_input.is_joke_turn):
        user_parts.append("Use a banking-related joke or money-themed playful line if you answer with humor.")

    if prompt_input.name:
        user_parts.append(f"User name: {prompt_input.name}")

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
