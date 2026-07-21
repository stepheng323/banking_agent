"""Locale updates driven by planner language detection."""

from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.locale import LocaleManager
from shared.types.planner import PlannerOutput


async def _resolve_planner_detected_locale(
    *,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    current_locale: str,
    redis_client: object | None,
) -> str:
    detected_language = getattr(planner_output, "detected_language", None)
    if not detected_language:
        return current_locale

    resolved_locale = await LocaleManager.resolve_turn_locale(
        phone_number=state_view.phone_number,
        current_locale=current_locale,
        detected_language=detected_language,
        confidence=float(getattr(planner_output, "confidence", 1.0) or 0.0),
        source="planner",
        # Some planner contexts intentionally provide a read-only cache adapter.
        # Do not let its mere truthiness trigger LocaleManager's write path.
        persist_detection=bool(redis_client and callable(getattr(redis_client, "set", None))),
    )
    return resolved_locale.value


__all__ = ["_resolve_planner_detected_locale"]
