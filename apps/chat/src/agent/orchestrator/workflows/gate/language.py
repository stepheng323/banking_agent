"""Language-switch helpers for the gate workflow."""

import re

_SUPPORTED_SWITCHABLE_LOCALES = {"en", "pcm", "yo", "ha", "ig"}
_ENGLISH_FASTPATH_CUE_RE = re.compile(
    r"^(?:show|list|view|get|check|what(?:'s| is)|how much|send|transfer|pay|buy|recharge|top\s*up|topup|"
    r"link|unlink|set|make)\b",
    re.IGNORECASE,
)
_LANGUAGE_SWITCH_EXACT: dict[str, str] = {
    "switch to english": "en",
    "speak english": "en",
    "reply in english": "en",
    "continue in english": "en",
    "use english": "en",
    "switch to pidgin": "pcm",
    "switch to naija": "pcm",
    "speak pidgin": "pcm",
    "reply in pidgin": "pcm",
    "continue in pidgin": "pcm",
    "use pidgin": "pcm",
    "abeg yarn for pidgin": "pcm",
    "make we yarn pidgin": "pcm",
    "switch to yoruba": "yo",
    "speak yoruba": "yo",
    "reply in yoruba": "yo",
    "continue in yoruba": "yo",
    "use yoruba": "yo",
    "so yoruba": "yo",
    "so ede yoruba": "yo",
    "ba mi soro ni ede yoruba": "yo",
    "switch to hausa": "ha",
    "speak hausa": "ha",
    "reply in hausa": "ha",
    "continue in hausa": "ha",
    "use hausa": "ha",
    "yi magana da hausa": "ha",
    "switch to igbo": "ig",
    "speak igbo": "ig",
    "reply in igbo": "ig",
    "continue in igbo": "ig",
    "use igbo": "ig",
    "kwuo igbo": "ig",
}


def _normalize_user_text(message_text: str) -> str:
    return re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")


def _allow_phrase_heavy_fastpath(message_text: str, locale: str) -> bool:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False
    if locale == "en":
        return True
    return bool(_ENGLISH_FASTPATH_CUE_RE.match(normalized))


def _resolve_explicit_language_switch(message_text: str) -> str | None:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return None
    requested = _LANGUAGE_SWITCH_EXACT.get(normalized)
    if requested in _SUPPORTED_SWITCHABLE_LOCALES:
        return requested
    return None


def _looks_like_language_switch_request(message_text: str, requested_locale: str | None = None) -> bool:
    if _resolve_explicit_language_switch(message_text) is not None:
        return True

    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False

    locale_aliases = {
        "en": ("english",),
        "pcm": ("pidgin", "naija"),
        "yo": ("yoruba",),
        "ha": ("hausa",),
        "ig": ("igbo", "ibo"),
    }
    target_aliases = locale_aliases.get(requested_locale or "", ())
    if not target_aliases:
        target_aliases = tuple(alias for aliases in locale_aliases.values() for alias in aliases)

    has_locale_name = any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in target_aliases)
    if not has_locale_name:
        return False

    return bool(re.search(r"\b(?:switch|speak|reply|continue|use|talk|chat|yarn|answer)\b", normalized))


__all__ = [
    "_allow_phrase_heavy_fastpath",
    "_looks_like_language_switch_request",
    "_resolve_explicit_language_switch",
]
