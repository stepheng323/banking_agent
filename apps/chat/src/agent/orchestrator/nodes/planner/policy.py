"""Planner locale and policy helper functions."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import MetaIntent
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.i18n import LocaleManager, render_message, render_policy_notice
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_EXECUTOR_LABELS = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "query": "transaction query",
    "account": "account actions",
    "support": "support request",
    "faq": "banking help",
    "beneficiary": "beneficiary management",
}

META_RESPONSE_KEY_TO_INTENT: dict[str, MetaIntent] = {
    "conversational.identity": MetaIntent.IDENTITY,
    "conversational.brand_origin": MetaIntent.BRAND_ORIGIN,
    "conversational.capability_question": MetaIntent.CAPABILITIES,
    "conversational.out_of_scope": MetaIntent.LIMITS,
}

UNSUPPORTED_CAPABILITY_PATTERNS: dict[str, list[str]] = {
    "Financial advice": ["advice", "advise", "what should i do", "recommendation"],
    "Investments": ["invest", "investment", "stocks", "mutual fund", "crypto"],
    "International transfers": ["international transfer", "send abroad", "swift", "dollar transfer", "usd"],
    "Scheduled or recurring transfers": ["schedule", "scheduled", "recurring", "every week", "every month"],
    "All-time transaction history": ["all-time", "all time", "entire history", "lifetime history"],
    "PDF exports": ["pdf", "export statement", "download statement"],
    "CSV exports": ["csv", "export csv", "download csv"],
}

UNSUPPORTED_CAPABILITY_ALTERNATIVES: dict[str, list[str]] = {
    "Financial advice": ["review recent transactions", "check balances"],
    "Investments": ["send money", "review recent transactions"],
    "International transfers": ["send money"],
    "Scheduled or recurring transfers": ["one-time transfer"],
    "All-time transaction history": ["review recent transactions"],
    "PDF exports": ["review recent transactions"],
    "CSV exports": ["review recent transactions"],
}


def _detect_unsupported_capabilities(message_text: str) -> list[str]:
    """Resolve unsupported capabilities from planner-owned phrase patterns."""
    text = message_text.lower().strip()

    if not text:
        return []

    profile = get_cached_assistant_profile()
    configured_unsupported = profile.unsupported_capabilities
    pattern_map = UNSUPPORTED_CAPABILITY_PATTERNS

    detected_set: set[str] = set()
    for capability, patterns in pattern_map.items():
        if not patterns:
            continue
        normalized_patterns = [p.lower().strip() for p in patterns if p and p.strip()]
        if any(pattern in text for pattern in normalized_patterns):
            detected_set.add(capability)

    # Deterministic order for stable output/tests.
    ordered_detected = [cap for cap in configured_unsupported if cap in detected_set]
    return ordered_detected


def _resolve_unsupported_alternatives(unsupported: list[str]) -> list[str]:
    """Resolve up to two unique alternatives for planner notices."""
    alternatives: list[str] = []

    for capability in unsupported:
        cap_alts = UNSUPPORTED_CAPABILITY_ALTERNATIVES.get(capability, [])
        for alt in cap_alts:
            if alt and alt not in alternatives:
                alternatives.append(alt)
            if len(alternatives) >= 2:
                return alternatives
    return alternatives


def _build_locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    """Prepare loaded_context patch with updated locale."""
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _detected_locale_value(planner_output: Any) -> str | None:
    """Resolve detected locale value from planner output when present."""
    detected_language = getattr(planner_output, "detected_language", None)
    if not isinstance(detected_language, str) or not detected_language:
        return None
    return cast(str, LocaleManager.from_detection(detected_language).value)


def _build_policy_notice(message_text: str, planner_output: Any, locale: str = "en") -> str | None:
    if not planner_output or not planner_output.tasks:
        return None

    unsupported = _detect_unsupported_capabilities(message_text)
    if not unsupported:
        return None
    logger.info("unsupported_detected", capabilities=unsupported)

    supported_labels = []
    for executor in {t.executor for t in planner_output.tasks if t.executor in SUPPORTED_EXECUTOR_LABELS}:
        supported_labels.append(SUPPORTED_EXECUTOR_LABELS[executor])

    if not supported_labels:
        return None

    supported_text = ", ".join(sorted(supported_labels))
    unsupported_text = ", ".join(unsupported)
    alternatives = _resolve_unsupported_alternatives(unsupported)
    return cast(
        str,
        render_policy_notice(
            locale=locale,
            supported_text=supported_text,
            unsupported_text=unsupported_text,
            alternatives=alternatives,
        ),
    )


def _build_policy_aware_greeting(locale: str) -> str:
    return cast(str, render_message("conversational.greeting", locale))


def _meta_intent_from_response_key(response_key: str | None) -> MetaIntent | None:
    if not response_key:
        return None
    return META_RESPONSE_KEY_TO_INTENT.get(response_key)


__all__ = [
    "_build_locale_update",
    "_build_policy_aware_greeting",
    "_build_policy_notice",
    "_detected_locale_value",
    "_detect_unsupported_capabilities",
    "_meta_intent_from_response_key",
    "_resolve_unsupported_alternatives",
]
