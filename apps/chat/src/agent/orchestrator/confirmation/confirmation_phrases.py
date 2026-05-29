"""Locale-specific confirmation and rejection phrase tables."""

from __future__ import annotations

import re

from apps.chat.src.agent.orchestrator.confirmation.confirmation_models import ConfirmationPromptKind
from shared.i18n.locale import LocaleManager
from shared.i18n.models import LocaleCode

SUPPORTED_CONFIRMATION_LOCALES = {
    LocaleCode.EN,
    LocaleCode.PCM,
    LocaleCode.YO,
    LocaleCode.HA,
    LocaleCode.IG,
}

APPROVE_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "yep please",
        "ok",
        "okay",
        "sure",
        "sure please",
        "proceed",
        "go ahead",
        "confirm",
    },
    LocaleCode.PCM: {"yes na", "abeg proceed", "ok na"},
    LocaleCode.YO: {"beeni", "o dara", "siwaju"},
    LocaleCode.HA: {"na'am", "naam", "eh"},
    LocaleCode.IG: {"ee", "kwe", "ga n'ihu", "gaa n'ihu"},
}

REJECT_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {
        "no",
        "no thanks",
        "no thank you",
        "not now",
        "later",
        "do not proceed",
        "don't proceed",
    },
    LocaleCode.PCM: {"no o", "no abeg", "not now", "later"},
    LocaleCode.YO: {"rara", "ma se", "dawoduro"},
    LocaleCode.HA: {"a'a", "a a", "dakatar", "ba yanzu ba"},
    LocaleCode.IG: {"mba", "kwusi", "ugbua a"},
}

CANCEL_PHRASES_BY_LOCALE: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"cancel", "abort", "stop", "nevermind", "never mind"},
    LocaleCode.PCM: {"cancel", "abort", "stop", "commot", "no do again"},
    LocaleCode.YO: {"fagile", "da duro", "ma se", "dawoduro"},
    LocaleCode.HA: {"soke", "dakatar"},
    LocaleCode.IG: {"kagbuo", "kwusi"},
}

_PROMPT_APPROVE_EXTRAS: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]] = {
    "resume_prompt": {
        LocaleCode.EN: {
            "continue",
            "continue it",
            "continue please",
            "resume",
            "resume it",
            "resume please",
            "please continue",
            "please resume",
            "go back",
            "that transfer",
            "continue the transfer",
        },
        LocaleCode.PCM: {"abeg continue", "continue am", "resume am"},
        LocaleCode.YO: {"tesiwaju", "tesiwaju e", "tesiwaju jowo"},
        LocaleCode.HA: {"ci gaba", "ci gaba da shi"},
        LocaleCode.IG: {"bido ya", "continue ya"},
    },
    "beneficiary_save": {
        LocaleCode.EN: {"save", "save it", "save this", "save beneficiary"},
        LocaleCode.PCM: {"save am"},
        LocaleCode.YO: {"fipamo", "pamo", "toju"},
        LocaleCode.HA: {"ajiye", "adana"},
        LocaleCode.IG: {"chekwa", "debe"},
    },
    "transaction_confirmation": {},
    "amount_suggestion": {},
}

_PROMPT_REJECT_EXTRAS: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]] = {
    "resume_prompt": {
        LocaleCode.EN: {"leave it", "leave it please", "dismiss", "cancel that", "forget it"},
        LocaleCode.PCM: {"leave am", "forget am", "cancel am"},
        LocaleCode.YO: {"fagile"},
        LocaleCode.HA: {"soke"},
        LocaleCode.IG: {"kagbuo"},
    },
    "beneficiary_save": {
        LocaleCode.EN: {"skip", "dont save", "don't save", "leave it", "ignore"},
        LocaleCode.PCM: {"no save am"},
    },
    "transaction_confirmation": {},
    "amount_suggestion": {},
}

_TRIM_CHARS = '.,!?;:"`~()[]{}'


def normalize_confirmation_text(text: str | None) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return compact.strip(_TRIM_CHARS)


def normalize_confirmation_locale(value: str | LocaleCode | None) -> LocaleCode | None:
    if isinstance(value, LocaleCode):
        return value if value in SUPPORTED_CONFIRMATION_LOCALES else None
    if not value:
        return None
    normalized = LocaleManager.normalize(str(value).strip().lower())
    return normalized if normalized in SUPPORTED_CONFIRMATION_LOCALES else None


def confirmation_approve_phrases(prompt_kind: ConfirmationPromptKind) -> dict[LocaleCode, set[str]]:
    return _phrases_for_kind(APPROVE_PHRASES_BY_LOCALE, _PROMPT_APPROVE_EXTRAS, prompt_kind)


def confirmation_reject_phrases(prompt_kind: ConfirmationPromptKind) -> dict[LocaleCode, set[str]]:
    return _phrases_for_kind(REJECT_PHRASES_BY_LOCALE, _PROMPT_REJECT_EXTRAS, prompt_kind)


def confirmation_locale_candidates(locale: LocaleCode | None) -> list[LocaleCode]:
    ordered: list[LocaleCode] = []
    if locale in SUPPORTED_CONFIRMATION_LOCALES:
        ordered.append(locale)
    for fallback in (LocaleCode.EN, LocaleCode.PCM, LocaleCode.YO, LocaleCode.HA, LocaleCode.IG):
        if fallback not in ordered:
            ordered.append(fallback)
    return ordered


def _phrases_for_kind(
    base: dict[LocaleCode, set[str]],
    extras: dict[ConfirmationPromptKind, dict[LocaleCode, set[str]]],
    prompt_kind: ConfirmationPromptKind,
) -> dict[LocaleCode, set[str]]:
    result = {locale: set(phrases) for locale, phrases in base.items()}
    for locale, phrases in extras.get(prompt_kind, {}).items():
        result.setdefault(locale, set()).update(phrases)
    return result


__all__ = [
    "APPROVE_PHRASES_BY_LOCALE",
    "CANCEL_PHRASES_BY_LOCALE",
    "REJECT_PHRASES_BY_LOCALE",
    "SUPPORTED_CONFIRMATION_LOCALES",
    "confirmation_approve_phrases",
    "confirmation_locale_candidates",
    "confirmation_reject_phrases",
    "normalize_confirmation_locale",
    "normalize_confirmation_text",
]
