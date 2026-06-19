"""Locale state helpers for gate decisions."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.state.state_view import GateStateView
from banking.presentation.i18n.locale import LocaleManager


def _locale_update(state_view: GateStateView, locale: str) -> dict[str, Any]:
    loaded_context = dict(state_view.loaded_context_or_empty)
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _current_locale(state_view: GateStateView) -> str:
    return LocaleManager.normalize(state_view.loaded_context_or_empty.get("language")).value


async def _effective_response_locale(
    *,
    state_view: GateStateView,
    redis_client: Any | None,
    detected_language: str | None,
) -> tuple[str, dict[str, Any]]:
    locale = _current_locale(state_view)
    if not detected_language:
        return locale, {}

    detected_locale = LocaleManager.from_detection(detected_language).value
    if detected_locale == locale:
        return locale, {}

    if redis_client and await LocaleManager.is_explicit_locale(state_view.phone_number):
        return locale, {}

    return detected_locale, _locale_update(state_view, detected_locale)
