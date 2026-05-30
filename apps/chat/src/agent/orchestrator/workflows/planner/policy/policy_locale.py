"""Locale helpers for planner policy flows."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message

SUPPORTED_EXECUTOR_LABELS = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "query": "transaction query",
    "account": "account actions",
    "support": "support request",
    "faq": "banking help",
    "beneficiary": "beneficiary management",
    "schedule": "scheduled transaction management",
}
SUPPORTED_EXECUTOR_LABELS_BY_LOCALE = {
    "pcm": {
        "transfer": "money transfer",
        "airtime": "airtime purchase",
        "data": "data purchase",
        "query": "transaction query",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "yo": {
        "transfer": "transfer owo",
        "airtime": "rira airtime",
        "data": "rira data",
        "query": "wiwa transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "ha": {
        "transfer": "transfer kudi",
        "airtime": "sayan airtime",
        "data": "sayan data",
        "query": "binciken transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
    "ig": {
        "transfer": "transfer ego",
        "airtime": "izuta airtime",
        "data": "izuta data",
        "query": "nyocha transaction",
        "account": "account actions",
        "support": "support request",
        "faq": "banking help",
        "beneficiary": "beneficiary management",
        "schedule": "scheduled transaction management",
    },
}


def _locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in SUPPORTED_EXECUTOR_LABELS_BY_LOCALE else "en"


def _supported_executor_label(executor: str, locale: str) -> str:
    return SUPPORTED_EXECUTOR_LABELS_BY_LOCALE.get(_locale_key(locale), {}).get(
        executor,
        SUPPORTED_EXECUTOR_LABELS[executor],
    )


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


def _build_policy_aware_greeting(locale: str) -> str:
    return cast(str, render_message("conversational.greeting", locale))


__all__ = [
    "SUPPORTED_EXECUTOR_LABELS",
    "SUPPORTED_EXECUTOR_LABELS_BY_LOCALE",
    "_build_locale_update",
    "_build_policy_aware_greeting",
    "_detected_locale_value",
    "_locale_key",
    "_supported_executor_label",
]
