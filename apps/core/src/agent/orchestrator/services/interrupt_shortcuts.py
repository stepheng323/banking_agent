"""Deterministic shortcut rules for interrupt routing.

These rules are intentionally narrow and high-precision:
- confirmation approve/reject on short utterances only
- status query recap/requirements for supported locales
- fallback to LLM router when uncertain
"""

from __future__ import annotations

import re
from typing import Literal

from shared.i18n import LocaleManager
from shared.i18n.models import LocaleCode
from shared.types.planner import InterruptRouteDecision

SUPPORTED_SHORTCUT_LOCALES = {
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

CONFIRM_APPROVE_PHRASES: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"yes", "ok", "okay", "proceed", "go ahead", "confirm"},
    LocaleCode.PCM: {"yes na", "abeg proceed", "ok na"},
    LocaleCode.YO: {"beeni", "o dara", "siwaju"},
    LocaleCode.HA: {"na'am", "naam", "eh"},
    LocaleCode.IG: {"ee", "kwe", "ga n'ihu"},
}

CONFIRM_REJECT_PHRASES: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"no", "cancel", "stop", "not now", "later", "do not proceed", "don't proceed"},
    LocaleCode.PCM: {"no o", "no abeg", "not now", "later", "cancel"},
    LocaleCode.YO: {"rara", "ma se", "dawoduro", "ko to bayi"},
    LocaleCode.HA: {"a'a", "a a", "dakatar", "ba yanzu ba"},
    LocaleCode.IG: {"mba", "kwusi", "ugbua a"},
}

STATUS_RECAP_PHRASES: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"where did we stop", "where are we", "recap", "which step"},
    LocaleCode.PCM: {"where we stop", "where we dey", "which step"},
    LocaleCode.YO: {"nibo la duro", "ibo la duro", "ta ni ipele"},
    LocaleCode.HA: {"ina muka tsaya", "a ina muka tsaya"},
    LocaleCode.IG: {"ebe ka anyi kwusiri", "ebe anyi kwusiri"},
}

STATUS_REQUIREMENTS_PHRASES: dict[LocaleCode, set[str]] = {
    LocaleCode.EN: {"what do you need from me", "what do you need", "what next", "what is needed"},
    LocaleCode.PCM: {"wetin remain", "wetin you need from me", "wetin you need", "wetin next"},
    LocaleCode.YO: {"kini mo tun fi ranse", "kini e nilo lowo mi", "kini mo se tele"},
    LocaleCode.HA: {"me ake bukata daga gare ni", "menene na gaba", "me ya rage"},
    LocaleCode.IG: {"gini ka ichoro n'aka m", "gini foduru"},
}

_UPDATE_HINT_RE = re.compile(r"\b(change|update|edit|instead|amount|bank|account|recipient|beneficiary)\b")


def _normalize_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return compact.strip('.,!?;:"`~()[]{}')


def _detected_language(locale: LocaleCode) -> str:
    return {
        LocaleCode.EN: "English",
        LocaleCode.PCM: "Pidgin",
        LocaleCode.YO: "Yoruba",
        LocaleCode.HA: "Hausa",
        LocaleCode.IG: "Igbo",
    }.get(locale, "English")


def _build_decision(
    *,
    locale: LocaleCode,
    decision: Literal[
        "continue_flow", "switch_intent", "cancel", "unclear", "approve_flow", "reject_flow", "status_query"
    ],
    reason: str,
    status_query_type: Literal["recap", "requirements"] | None = None,
) -> InterruptRouteDecision:
    return InterruptRouteDecision(
        decision=decision,
        confidence=0.99,
        detected_language=_detected_language(locale),
        target_intent=None,
        target_mode=None,
        status_query_type=status_query_type,
        reason=reason,
    )


def _resolve_interrupt_shortcut(
    text: str,
    interrupt_kind: str,
    locale: LocaleCode | None,
) -> tuple[InterruptRouteDecision | None, str]:
    if locale not in SUPPORTED_SHORTCUT_LOCALES:
        return None, "unsupported_locale"

    normalized = _normalize_text(text)
    if not normalized:
        return None, "no_match"

    # Status-query shortcuts apply to any pending interrupt kind.
    if normalized in STATUS_RECAP_PHRASES.get(locale, set()):
        return (
            _build_decision(
                locale=locale,
                decision="status_query",
                status_query_type="recap",
                reason="shortcut_status_recap",
            ),
            "matched",
        )
    if normalized in STATUS_REQUIREMENTS_PHRASES.get(locale, set()):
        return (
            _build_decision(
                locale=locale,
                decision="status_query",
                status_query_type="requirements",
                reason="shortcut_status_requirements",
            ),
            "matched",
        )

    # Confirmation shortcuts only.
    if interrupt_kind != "confirmation":
        return None, "no_match"

    tokens = normalized.split()
    if len(tokens) > 6 or len(normalized) > 64:
        return None, "ambiguous"
    if re.search(r"\d", normalized):
        return None, "guardrail_blocked"
    if _UPDATE_HINT_RE.search(normalized):
        return None, "guardrail_blocked"

    if normalized in CONFIRM_APPROVE_PHRASES.get(locale, set()):
        return (
            _build_decision(locale=locale, decision="approve_flow", reason="shortcut_confirmation_approve"),
            "matched",
        )
    if normalized in CONFIRM_REJECT_PHRASES.get(locale, set()):
        return (
            _build_decision(locale=locale, decision="reject_flow", reason="shortcut_confirmation_reject"),
            "matched",
        )

    return None, "no_match"


def resolve_interrupt_shortcut(
    text: str,
    interrupt_kind: str,
    locale: LocaleCode | None,
) -> InterruptRouteDecision | None:
    """Resolve deterministic shortcut decision, or None when uncertain."""
    decision, _ = _resolve_interrupt_shortcut(text=text, interrupt_kind=interrupt_kind, locale=locale)
    return decision


def resolve_interrupt_shortcut_with_reason(
    text: str,
    interrupt_kind: str,
    locale: LocaleCode | None,
) -> tuple[InterruptRouteDecision | None, str]:
    """Resolve shortcut decision and miss reason for observability."""
    return _resolve_interrupt_shortcut(text=text, interrupt_kind=interrupt_kind, locale=locale)


def resolve_shortcut_locale(value: str | LocaleCode | None) -> LocaleCode | None:
    """Return deterministic shortcut locale, or None for unsupported/unknown."""
    if isinstance(value, LocaleCode):
        return value if value in SUPPORTED_SHORTCUT_LOCALES else None
    if not value:
        return None

    token = value.strip().lower()
    if token not in _LOCALE_ALIASES:
        return None

    normalized = LocaleManager.normalize(token)
    return normalized if normalized in SUPPORTED_SHORTCUT_LOCALES else None
