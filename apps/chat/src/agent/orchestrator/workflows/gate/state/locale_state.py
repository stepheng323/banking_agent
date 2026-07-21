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
    confidence: float = 1.0,
) -> tuple[str, dict[str, Any]]:
    locale = _current_locale(state_view)
    if not detected_language:
        return locale, {}

    detected_locale = LocaleManager.parse_locale_name(detected_language)
    if detected_locale is None:
        return locale, {}
    if detected_locale.value == locale:
        return locale, {}

    resolved = await LocaleManager.resolve_turn_locale(
        phone_number=state_view.phone_number,
        current_locale=locale,
        detected_language=detected_language,
        confidence=confidence,
        source="semantic_router",
        persist_detection=bool(redis_client and callable(getattr(redis_client, "set", None))),
    )
    if resolved.value == locale:
        return locale, {}

    return resolved.value, _locale_update(state_view, resolved.value)
