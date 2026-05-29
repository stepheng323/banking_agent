"""Deterministic meta-response classifiers for the gate workflow."""

import re
from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import detect_unsupported_capability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from shared.branding import brand_name_aliases, legacy_brand_names, normalize_brand_name
from shared.i18n.message_keys import MessageKey

DETERMINISTIC_GREETING_EXACT = {
    "hi",
    "hello",
    "hey",
    "how far",
    "sup",
    "what's up",
    "what s up",
    "good morning",
    "good afternoon",
    "good evening",
}
DETERMINISTIC_APPRECIATION_EXACT = {
    "thanks",
    "thank you",
    "thankyou",
}
DETERMINISTIC_CHECKIN_EXACT = {
    "are you there",
    "are you online",
    "how are you",
    "how is it going",
    "how's it going",
    "how s it going",
    "you online",
    "you there",
}
DETERMINISTIC_ADDRESSED_GREETING_RE = re.compile(
    r"^(?P<greeting>hi|hello|hey|good morning|good afternoon|good evening)"
    r"\s*[,;:!-]?\s+(?P<address>[a-z][a-z0-9 ._-]{0,48})$",
    re.IGNORECASE,
)
GENERIC_GREETING_ADDRESSES = {
    "abeg",
    "boss",
    "bro",
    "dear",
    "fam",
    "friend",
    "g",
    "guy",
    "my g",
    "my guy",
    "oga",
    "please",
    "pls",
    "sis",
    "team",
    "there",
}
ADDRESSED_GREETING_NON_NAME_TOKENS = {
    "account",
    "airtime",
    "balance",
    "buy",
    "can",
    "check",
    "data",
    "do",
    "for",
    "from",
    "get",
    "help",
    "how",
    "i",
    "pay",
    "please",
    "pls",
    "send",
    "show",
    "to",
    "transaction",
    "transactions",
    "transfer",
    "what",
    "where",
    "with",
    "you",
}
DETERMINISTIC_IDENTITY_EXACT = {
    "who are you",
    "what is your name",
    "what s your name",
    "what's your name",
}
DETERMINISTIC_BRAND_ORIGIN_EXACT = {
    "who created you",
    "who built you",
    "who made you",
}
DETERMINISTIC_CAPABILITY_EXACT = {
    "what can you do",
    "what do you do",
    "what can you help me with",
    "what do you handle",
}
DETERMINISTIC_CAPABILITY_PATTERNS = (
    re.compile(
        r"^(?:can|could|will|would)\s+you\s+(?:help|assist)(?:\s+me)?\s+"
        r"(?:send|transfer|pay|buy|recharge|top\s*up|check|show|view|list)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:can|could|will|would)\s+you\s+(?:help|assist)(?:\s+me)?\s+(?:with\s+)?"
        r"(?:funds?|money|transfers?|payments?|airtime|data|balances?|transactions?)\b",
        re.IGNORECASE,
    ),
)
DETERMINISTIC_LOCALE_META_EXACT: dict[str, tuple[MessageKey, str]] = {
    # Pidgin
    "wetin you fit do": ("conversational.capability_question", "pcm"),
    "who you be": ("conversational.identity", "pcm"),
    "who build you": ("conversational.brand_origin", "pcm"),
    "abeg": ("conversational.checkin", "pcm"),
    "how body": ("conversational.checkin", "pcm"),
    "how you dey": ("conversational.checkin", "pcm"),
    "you dey": ("conversational.checkin", "pcm"),
    # Yoruba
    "pele o": ("conversational.greeting", "yo"),
    "e se": ("conversational.appreciation", "yo"),
    "ese": ("conversational.appreciation", "yo"),
    "ta lo je": ("conversational.identity", "yo"),
    "kini o le se": ("conversational.capability_question", "yo"),
    "kini o ma n se": ("conversational.capability_question", "yo"),
    # Hausa
    "sannu": ("conversational.greeting", "ha"),
    "nagode": ("conversational.appreciation", "ha"),
    "kai wa ne": ("conversational.identity", "ha"),
    "me zaka iya yi": ("conversational.capability_question", "ha"),
    # Igbo
    "ndewo": ("conversational.greeting", "ig"),
    "dalu": ("conversational.appreciation", "ig"),
    "onye ka i bu": ("conversational.identity", "ig"),
    "gini ka i nwere ike ime": ("conversational.capability_question", "ig"),
}
DETERMINISTIC_LOCALE_META_PATTERNS: tuple[tuple[re.Pattern[str], tuple[MessageKey, str]], ...] = (
    (
        re.compile(
            r"^(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)[\s,]+)?"
            r"how\s+far"
            r"(?:\s+(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)|now|na))?$",
            re.IGNORECASE,
        ),
        ("conversational.greeting", "pcm"),
    ),
    (
        re.compile(
            r"^(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)[\s,]+)?"
            r"(?:how\s+you\s+dey|how\s+body|you\s+dey)"
            r"(?:\s+(?:now|na))?$",
            re.IGNORECASE,
        ),
        ("conversational.checkin", "pcm"),
    ),
)


@dataclass(frozen=True, slots=True)
class DeterministicMetaResponse:
    response_key: MessageKey
    response_locale: str | None = None
    params: dict[str, object] | None = None


def _meta_response(
    response_key: MessageKey,
    response_locale: str | None = None,
    params: dict[str, object] | None = None,
) -> DeterministicMetaResponse:
    return DeterministicMetaResponse(response_key=response_key, response_locale=response_locale, params=params)


def _addressed_name_param(address: str) -> dict[str, object]:
    return {"addressed_name": " ".join(token.capitalize() for token in address.split())}


def _classify_addressed_greeting(normalized: str) -> DeterministicMetaResponse | None:
    match = DETERMINISTIC_ADDRESSED_GREETING_RE.match(normalized)
    if not match:
        return None

    address = normalize_brand_name(match.group("address"))
    if not address:
        return _meta_response("conversational.greeting")
    if address in brand_name_aliases() or address in GENERIC_GREETING_ADDRESSES:
        return _meta_response("conversational.greeting")
    if address in legacy_brand_names():
        return _meta_response("conversational.identity_correction", params=_addressed_name_param(address))
    address_tokens = address.split()
    if len(address_tokens) > 3 or set(address_tokens) & ADDRESSED_GREETING_NON_NAME_TOKENS:
        return None
    return _meta_response("conversational.identity_correction", params=_addressed_name_param(address))


def _is_brand_origin_lookup(normalized: str) -> bool:
    for alias in brand_name_aliases() | legacy_brand_names():
        if normalized in {
            f"what does {alias} mean",
            f"what is the meaning of {alias}",
            f"what's the meaning of {alias}",
            f"what s the meaning of {alias}",
            f"meaning of {alias}",
            f"why are you called {alias}",
            f"why are you named {alias}",
            f"where did the name {alias} come from",
        }:
            return True
    return False


def _is_brand_product_lookup(normalized: str) -> bool:
    for alias in brand_name_aliases():
        if normalized in {
            f"what is {alias}",
            f"what's {alias}",
            f"what s {alias}",
            f"tell me about {alias}",
        }:
            return True
    return False


def classify_deterministic_meta_response(message_text: str) -> DeterministicMetaResponse | None:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if normalized in DETERMINISTIC_LOCALE_META_EXACT:
        response_key, response_locale = DETERMINISTIC_LOCALE_META_EXACT[normalized]
        return _meta_response(response_key, response_locale)
    for pattern, response in DETERMINISTIC_LOCALE_META_PATTERNS:
        if pattern.match(normalized):
            response_key, response_locale = response
            return _meta_response(response_key, response_locale)
    if normalized in DETERMINISTIC_GREETING_EXACT:
        return _meta_response("conversational.greeting")
    if normalized in DETERMINISTIC_APPRECIATION_EXACT:
        return _meta_response("conversational.appreciation")
    if normalized in DETERMINISTIC_CHECKIN_EXACT:
        return _meta_response("conversational.checkin")
    addressed_greeting = _classify_addressed_greeting(normalized)
    if addressed_greeting:
        return addressed_greeting
    if normalized in DETERMINISTIC_IDENTITY_EXACT:
        return _meta_response("conversational.identity")
    if _is_brand_product_lookup(normalized):
        return _meta_response("conversational.identity")
    if normalized in DETERMINISTIC_BRAND_ORIGIN_EXACT or _is_brand_origin_lookup(normalized):
        return _meta_response("conversational.brand_origin")
    unsupported_capability = detect_unsupported_capability(normalized)
    if unsupported_capability is not None:
        return _meta_response(
            "capability.unsupported_unavailable",
            params=unsupported_capability_params(unsupported_capability),
        )
    if normalized in DETERMINISTIC_CAPABILITY_EXACT:
        return _meta_response("conversational.capability_question")
    if any(pattern.match(normalized) for pattern in DETERMINISTIC_CAPABILITY_PATTERNS):
        return _meta_response("conversational.capability_question")
    return None


__all__ = [
    "DeterministicMetaResponse",
    "classify_deterministic_meta_response",
]
