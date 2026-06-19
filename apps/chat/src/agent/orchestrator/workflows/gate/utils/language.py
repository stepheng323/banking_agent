"""Language-switch helpers for the gate workflow."""

import re

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LocaleCode

_SUPPORTED_SWITCHABLE_LOCALES = {"en", "pcm", "yo", "ha", "ig"}
_ENGLISH_FASTPATH_CUE_RE = re.compile(
    r"^(?:show|list|view|get|check|what(?:'s| is)|how much|how many|send|transfer|pay|remit|split|buy|recharge|"
    r"top\s*up|topup|use|using|link|unlink|set|make)\b",
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

_LANGUAGE_SWITCH_REQUEST_RE = re.compile(
    r"\b(?:you\s+fit|can\s+you|could\s+you|abeg|please|pls)?\s*"
    r"(?:switch|speak|talk|chat|yarn|reply|answer|use|continue)\s+"
    r"(?:to|in|for)?\s*"
    r"(?P<locale>[a-z][a-z\s-]{1,40})\b",
    re.IGNORECASE,
)


def _normalize_user_text(message_text: str) -> str:
    return re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")


def _allow_phrase_heavy_fastpath(message_text: str, locale: str) -> bool:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False
    if locale == "en":
        return True
    return bool(_ENGLISH_FASTPATH_CUE_RE.match(normalized))


def _parse_locale_from_fragment(fragment: str) -> LocaleCode | None:
    locale = LocaleManager.parse_locale_name(fragment)
    if locale is not None:
        return locale

    tokens = re.findall(r"[a-z][a-z-]*", fragment.lower())
    for width in range(min(3, len(tokens)), 0, -1):
        for start in range(0, len(tokens) - width + 1):
            locale = LocaleManager.parse_locale_name(
                " ".join(tokens[start : start + width]),
                allow_fuzzy=True,
            )
            if locale is not None:
                return locale
    return None


def _resolve_explicit_language_switch(message_text: str) -> str | None:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return None
    requested = _LANGUAGE_SWITCH_EXACT.get(normalized)
    if requested in _SUPPORTED_SWITCHABLE_LOCALES:
        return requested
    for match in _LANGUAGE_SWITCH_REQUEST_RE.finditer(normalized):
        locale = _parse_locale_from_fragment(match.group("locale"))
        if locale is not None and locale.value in _SUPPORTED_SWITCHABLE_LOCALES:
            return locale.value
    return None


def _looks_like_language_switch_request(message_text: str, requested_locale: str | None = None) -> bool:
    if _resolve_explicit_language_switch(message_text) is not None:
        return True

    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False

    requested = LocaleManager.parse_locale_name(requested_locale) if requested_locale else None
    target_aliases = LocaleManager.aliases_for(requested) if requested is not None else ()
    if not target_aliases:
        target_aliases = tuple(
            alias for locale in _SUPPORTED_SWITCHABLE_LOCALES for alias in LocaleManager.aliases_for(locale)
        )

    has_locale_name = any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in target_aliases)
    if not has_locale_name:
        parsed = _parse_locale_from_fragment(normalized)
        has_locale_name = parsed is not None and (requested is None or parsed == requested)
    if not has_locale_name:
        return False

    return bool(re.search(r"\b(?:switch|speak|reply|continue|use|talk|chat|yarn|answer)\b", normalized))


__all__ = [
    "_allow_phrase_heavy_fastpath",
    "_looks_like_language_switch_request",
    "_resolve_explicit_language_switch",
]
