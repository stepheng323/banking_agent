"""Locale updates driven by planner language detection."""

from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LanguageDetectionSignal
from shared.types.planner import PlannerOutput


async def _resolve_planner_detected_locale(
    *,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    current_locale: str,
    redis_client: redis.Redis | None,
) -> str:
    detected_language = getattr(planner_output, "detected_language", None)
    if not detected_language:
        return current_locale

    if redis_client:
        signal = LanguageDetectionSignal(
            locale=LocaleManager.from_detection(detected_language),
            confidence=float(getattr(planner_output, "confidence", 1.0) or 0.0),
            source="planner",
            explicit=False,
        )
        resolved_locale = await LocaleManager.update_locale(state_view.phone_number, signal)
        return resolved_locale.value
    return LocaleManager.from_detection(detected_language).value


__all__ = ["_resolve_planner_detected_locale"]
