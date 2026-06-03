"""Locale-aware rendering parameters for unsupported capability boundaries."""

from collections.abc import Iterable, Mapping

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import UnsupportedCapability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    PLANNER_ALTERNATIVE_LABELS_BY_LOCALE,
    get_unsupported_capability,
    get_unsupported_capability_by_policy_label,
    locale_key,
    localized_supported_alternatives,
)


def unsupported_capability_label(capability: UnsupportedCapability, locale: str | None = None) -> str:
    return capability.labels_by_locale.get(locale_key(locale), capability.label)


def unsupported_capability_supported_alternatives(
    capability: UnsupportedCapability,
    locale: str | None = None,
) -> str:
    return capability.supported_alternatives_by_locale.get(
        locale_key(locale),
        capability.supported_alternatives if locale is None else localized_supported_alternatives(locale),
    )


def unsupported_capability_params(
    capability: UnsupportedCapability | Mapping[str, object],
    *,
    locale: str | None = None,
) -> dict[str, object]:
    if isinstance(capability, UnsupportedCapability):
        return {
            "capability_key": capability.key,
            "capability": unsupported_capability_label(capability, locale),
            "supported": unsupported_capability_supported_alternatives(capability, locale),
        }
    key = str(capability.get("key") or capability.get("capability_key") or "")
    registered = get_unsupported_capability(key)
    if registered is not None:
        return unsupported_capability_params(registered, locale=locale)
    return {
        "capability_key": key,
        "capability": str(capability.get("label") or capability.get("capability") or "that capability"),
        "supported": str(capability.get("supported") or localized_supported_alternatives(locale)),
    }


def format_planner_alternatives(
    capability_labels: Iterable[str],
    *,
    locale: str | None = None,
) -> list[str]:
    alternatives: list[str] = []
    localized = PLANNER_ALTERNATIVE_LABELS_BY_LOCALE.get(locale_key(locale), {})
    for label in capability_labels:
        capability = get_unsupported_capability_by_policy_label(label)
        if not capability:
            continue
        for alternative in capability.planner_alternatives:
            localized_alternative = localized.get(alternative, alternative)
            if localized_alternative and localized_alternative not in alternatives:
                alternatives.append(localized_alternative)
            if len(alternatives) >= 2:
                return alternatives
    return alternatives
