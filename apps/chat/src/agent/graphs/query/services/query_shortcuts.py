"""Locale-aware deterministic shortcuts for active query sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from shared.i18n import LocaleManager
from shared.i18n.models import LocaleCode

QueryShortcutKind = Literal["pagination", "actionable"]
QueryShortcutAction = Literal["show_more", "show_previous", "get_receipt", "report_issue"]

SUPPORTED_QUERY_SHORTCUT_LOCALES = {
    LocaleCode.EN,
    LocaleCode.PCM,
    LocaleCode.YO,
    LocaleCode.HA,
    LocaleCode.IG,
}
_LOCALE_ALIASES: dict[str, LocaleCode] = {
    "en": LocaleCode.EN,
    "english": LocaleCode.EN,
    "pcm": LocaleCode.PCM,
    "pidgin": LocaleCode.PCM,
    "nigerian pidgin": LocaleCode.PCM,
    "naija": LocaleCode.PCM,
    "yo": LocaleCode.YO,
    "yoruba": LocaleCode.YO,
    "ha": LocaleCode.HA,
    "hausa": LocaleCode.HA,
    "ig": LocaleCode.IG,
    "igbo": LocaleCode.IG,
    "ibo": LocaleCode.IG,
}


@dataclass(frozen=True)
class QueryShortcutDecision:
    """Structured query shortcut resolution."""

    kind: QueryShortcutKind
    action: QueryShortcutAction
    normalized_text: str


_SHORTCUTS_BY_LOCALE: dict[LocaleCode, dict[str, tuple[QueryShortcutKind, QueryShortcutAction]]] = {
    LocaleCode.EN: {
        "more": ("pagination", "show_more"),
        "next": ("pagination", "show_more"),
        "next page": ("pagination", "show_more"),
        "show more": ("pagination", "show_more"),
        "back": ("pagination", "show_previous"),
        "previous": ("pagination", "show_previous"),
        "previous page": ("pagination", "show_previous"),
        "receipt": ("actionable", "get_receipt"),
        "issue": ("actionable", "report_issue"),
        "report issue": ("actionable", "report_issue"),
    },
    LocaleCode.PCM: {
        "more": ("pagination", "show_more"),
        "next": ("pagination", "show_more"),
        "next page": ("pagination", "show_more"),
        "show more": ("pagination", "show_more"),
        "back": ("pagination", "show_previous"),
        "previous": ("pagination", "show_previous"),
        "previous page": ("pagination", "show_previous"),
        "receipt": ("actionable", "get_receipt"),
        "issue": ("actionable", "report_issue"),
        "report issue": ("actionable", "report_issue"),
    },
    LocaleCode.YO: {},
    LocaleCode.HA: {},
    LocaleCode.IG: {},
}


def _normalize_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return compact.strip('.,!?;:"`~()[]{}')


def resolve_query_shortcut_locale(value: str | LocaleCode | None) -> LocaleCode | None:
    """Return the locale used for deterministic query shortcuts."""
    if isinstance(value, LocaleCode):
        return value if value in SUPPORTED_QUERY_SHORTCUT_LOCALES else None
    if not value:
        return None

    token = value.strip().lower()
    if token not in _LOCALE_ALIASES:
        return None

    locale = LocaleManager.normalize(token)
    return locale if locale in SUPPORTED_QUERY_SHORTCUT_LOCALES else None


def resolve_query_shortcut_with_reason(
    text: str,
    locale: str | LocaleCode | None,
) -> tuple[QueryShortcutDecision | None, str]:
    """Resolve an active-query deterministic shortcut and explain misses."""
    resolved_locale = resolve_query_shortcut_locale(locale)
    if resolved_locale is None:
        return None, "unsupported_locale"

    normalized = _normalize_text(text)
    if not normalized:
        return None, "no_match"

    locale_shortcuts = _SHORTCUTS_BY_LOCALE.get(resolved_locale, {})
    shortcut = locale_shortcuts.get(normalized)
    if shortcut is None:
        return None, "no_match"

    kind, action = shortcut
    return QueryShortcutDecision(kind=kind, action=action, normalized_text=normalized), "matched"


def resolve_query_shortcut(
    text: str,
    locale: str | LocaleCode | None,
) -> QueryShortcutDecision | None:
    """Resolve an active-query deterministic shortcut."""
    decision, _ = resolve_query_shortcut_with_reason(text=text, locale=locale)
    return decision


__all__ = [
    "QueryShortcutAction",
    "QueryShortcutDecision",
    "QueryShortcutKind",
    "resolve_query_shortcut",
    "resolve_query_shortcut_locale",
    "resolve_query_shortcut_with_reason",
]
