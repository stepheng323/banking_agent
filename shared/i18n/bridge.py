"""Bridge helpers for migrating existing responses to deterministic i18n."""

from __future__ import annotations

from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message


def normalize_locale(locale: str | None) -> str:
    return LocaleManager.normalize(locale).value


def _join_two(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{items[0]} or {items[1]}"


def render_capability_limitation(
    *,
    locale: str | None,
    action_label: str,
    alternative_labels: list[str] | None = None,
    policy_message: str | None = None,
) -> str:
    """Render strict capability limitation text in target locale."""
    resolved_locale = normalize_locale(locale)
    del policy_message

    alternatives = [item for item in (alternative_labels or []) if item]

    if alternatives:
        return render_message(
            "capability.blocked_with_alternative",
            resolved_locale,
            {
                "action": action_label,
                "alternative": _join_two(alternatives),
            },
        )

    return render_message(
        "capability.blocked_no_alternative",
        resolved_locale,
        {"action": action_label},
    )


def render_policy_notice(
    *,
    locale: str | None,
    supported_text: str,
    unsupported_text: str,
    alternatives: list[str] | None = None,
) -> str:
    resolved_locale = normalize_locale(locale)
    if alternatives:
        return render_message(
            "planner.policy_notice_with_alternative",
            resolved_locale,
            {
                "supported": supported_text,
                "unsupported": unsupported_text,
                "alternative": _join_two(alternatives),
            },
        )

    return render_message(
        "planner.policy_notice_no_alternative",
        resolved_locale,
        {
            "supported": supported_text,
            "unsupported": unsupported_text,
        },
    )


def render_safe_capability_fallback(locale: str | None) -> str:
    return render_message("common.safe_capability_fallback", normalize_locale(locale))


def render_cancelled_prompt(locale: str | None) -> str:
    return render_message("common.cancelled_prompt", normalize_locale(locale))


def render_generic_capability_blocked(locale: str | None) -> str:
    return render_message("capability.generic_blocked", normalize_locale(locale))


def render_locale_switched(locale: str | None) -> str:
    return render_message("locale.switched", normalize_locale(locale))
