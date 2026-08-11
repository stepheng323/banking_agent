"""Conversation responder mode constants."""

from enum import Enum


class ConversationResponseMode(str, Enum):
    """The generation mode for a conversational reply."""

    SOCIAL_META = "social_meta"
    CLARIFY = "clarify"
    CAPABILITIES = "capabilities"
    CASUAL = "casual"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED_BOUNDARY = "unsupported_boundary"
    MELKOR_BOUNDARY = "melkor_boundary"
    CONTEXTUAL_WORKER = "contextual_worker"
    CONTEXTUAL_META = "contextual_meta"


SOCIAL_META_RESPONSE_KEYS = frozenset(
    {
        "conversational.greeting",
        "conversational.greeting_named",
        "conversational.checkin",
        "conversational.appreciation",
    }
)

SOCIAL_META_RESPONSE_KEY_CTX = "social_meta_response_key"
SOCIAL_META_RENDER_PARAMS_CTX = "social_meta_render_params"

__all__ = [
    "ConversationResponseMode",
    "SOCIAL_META_RENDER_PARAMS_CTX",
    "SOCIAL_META_RESPONSE_KEY_CTX",
    "SOCIAL_META_RESPONSE_KEYS",
    "map_response_key_to_mode",
]


def map_response_key_to_mode(key: str) -> ConversationResponseMode | None:
    """Map an explicitly conversational response key to its generation mode."""
    if key in SOCIAL_META_RESPONSE_KEYS:
        return ConversationResponseMode.SOCIAL_META
    if key == "conversational.casual_chat":
        return ConversationResponseMode.CASUAL
    if key == "conversational.capability_question":
        return ConversationResponseMode.CAPABILITIES
    if key in ("conversational.clarify", "planner.ambiguous"):
        return ConversationResponseMode.CLARIFY
    if key == "conversational.out_of_scope":
        return ConversationResponseMode.OUT_OF_SCOPE
    if key in {
        "conversational.unsupported_capability",
        "capability.unsupported_unavailable",
    }:
        return ConversationResponseMode.UNSUPPORTED_BOUNDARY
    if key == "meta.melkor_easter_egg":
        return ConversationResponseMode.MELKOR_BOUNDARY
    return None
