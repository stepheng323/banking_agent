"""Conversation responder intent constants."""

NON_BANKING_CONVERSATIONAL_INTENT = "non_banking_conversational"
SOCIAL_META_INTENT = "social_meta"

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
    "NON_BANKING_CONVERSATIONAL_INTENT",
    "SOCIAL_META_INTENT",
    "SOCIAL_META_RENDER_PARAMS_CTX",
    "SOCIAL_META_RESPONSE_KEY_CTX",
    "SOCIAL_META_RESPONSE_KEYS",
]
