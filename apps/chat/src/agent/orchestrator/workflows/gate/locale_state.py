"""Locale state helpers for gate decisions."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager


def _locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _current_locale(state: OrchestratorState) -> str:
    return LocaleManager.normalize((state.loaded_context or {}).get("language")).value


async def _effective_response_locale(
    *,
    state: OrchestratorState,
    redis_client: Any | None,
    detected_language: str | None,
) -> tuple[str, dict[str, Any]]:
    locale = _current_locale(state)
    if not detected_language:
        return locale, {}

    detected_locale = LocaleManager.from_detection(detected_language).value
    if detected_locale == locale:
        return locale, {}

    if redis_client and await LocaleManager.is_explicit_locale(state.phone_number):
        return locale, {}

    return detected_locale, _locale_update(state, detected_locale)
