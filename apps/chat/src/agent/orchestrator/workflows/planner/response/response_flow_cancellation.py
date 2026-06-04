"""Planner cancellation response handling."""

from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _build_locale_update
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_cancellation_response(
    *,
    state: OrchestratorState,
    state_view: PlannerStateView,
    planner_output: PlannerOutput,
    redis_client: redis.Redis | None,
    current_locale: str,
    detected_locale: str | None,
    locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
) -> dict[str, Any] | None:
    if not (getattr(planner_output, "is_cancellation", False) or planner_output.primary_intent == "cancel"):
        return None

    logger.info("planner_cancellation_detected", intent=planner_output.primary_intent)
    cancel_locale = detected_locale or current_locale
    cancel_locale_updates = (
        locale_updates if cancel_locale == current_locale else _build_locale_update(state_view, cancel_locale)
    )
    if not has_cancelable_state(state):
        return {
            "final_response": clarify_message(state, cancel_locale),
            **cancel_locale_updates,
            **context_read_updates,
        }
    cancel_message = cancelled_message(state, cancel_locale)
    reset_updates = await build_cancellation_reset_updates(state, redis_client)
    return {
        **reset_updates,
        "final_response": cancel_message,
        **cancel_locale_updates,
    }


__all__ = ["_build_cancellation_response"]
