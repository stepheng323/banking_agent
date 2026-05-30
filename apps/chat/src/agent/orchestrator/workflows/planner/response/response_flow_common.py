"""Shared helpers for planner no-task response handling."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _build_locale_update
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_text
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _resolved_locale_with_precedence(
    *,
    state: OrchestratorState,
    current_locale: str,
    detected_locale: str | None,
    redis_client: Any | None,
    locale_updates: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if detected_locale is None or detected_locale == current_locale:
        return current_locale, locale_updates
    if redis_client and await LocaleManager.is_explicit_locale(state.phone_number):
        return current_locale, locale_updates
    return detected_locale, _build_locale_update(state, detected_locale)


def _localized_planner_response(raw_response: str | None, locale: str) -> str:
    if not raw_response:
        return ""
    return cast(str, render_text(raw_response, locale))


async def _build_bounded_conversational_reply(
    *,
    state: OrchestratorState,
    text: str,
    locale: str,
    conversation_responder: Any | None,
) -> str | None:
    if conversation_responder is None:
        return None
    try:
        return await conversation_responder.generate_reply(
            text,
            {
                **(state.loaded_context or {}),
                "language": locale,
            },
            intent="non_banking_conversational",
        )
    except Exception as exc:
        logger.warning("conversation_responder_failed", error=str(exc))
        return None


__all__ = [
    "_build_bounded_conversational_reply",
    "_localized_planner_response",
    "_resolved_locale_with_precedence",
]
