"""Planner quoted replay shortcut flow helpers."""

from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_quoted_replay import (
    _build_quoted_replay_context,
    _build_quoted_replay_context_with_payload,
    _build_quoted_replay_execution_updates,
    _load_quoted_actionable_payload,
    _quoted_replay_clarify_response,
)
from shared.i18n import render_message
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _handle_quoted_replay_shortcut(
    *,
    state: OrchestratorState,
    config: RunnableConfig,
    task_planner: Any,
    text: str,
    current_locale: str,
    locale_updates: dict[str, Any],
    quoted_replay_min_confidence: float,
) -> dict[str, Any] | None:
    if not (state.has_quote and state.quoted_message_id and hasattr(task_planner, "interpret_quoted_replay")):
        return None

    quoted_payload = await _load_quoted_actionable_payload(state, config)
    quoted_context = (
        _build_quoted_replay_context_with_payload(state, quoted_payload)
        if quoted_payload is not None
        else _build_quoted_replay_context(state)
    )
    try:
        interpretation = cast(
            QuotedReplayInterpretation,
            await task_planner.interpret_quoted_replay(state.phone_number, text, context=quoted_context),
        )
        if interpretation.decision == "clarify":
            logger.info("quoted_replay_shortcut_clarify", reason=interpretation.reason)
            return {
                "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                "normalized_instruction": text,
                **locale_updates,
            }
        if interpretation.decision == "execute":
            if interpretation.confidence < quoted_replay_min_confidence:
                logger.info(
                    "quoted_replay_confidence_low",
                    decision=interpretation.decision,
                    confidence=interpretation.confidence,
                    min_confidence=quoted_replay_min_confidence,
                )
                return {
                    "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            if quoted_payload is None:
                logger.info("quoted_replay_actionable_payload_missing", quoted_message_id=state.quoted_message_id)
                return {
                    "final_response": render_message("conversational.clarify", current_locale),
                    "normalized_instruction": text,
                    **locale_updates,
                }
            replay_updates = _build_quoted_replay_execution_updates(
                state=state,
                text=text,
                interpretation=interpretation,
                locale_updates=locale_updates,
            )
            if replay_updates is not None:
                return replay_updates
            logger.info(
                "quoted_replay_insufficient_payload",
                decision=interpretation.decision,
                reason=interpretation.reason,
            )
            return {
                "final_response": _quoted_replay_clarify_response(interpretation, current_locale),
                "normalized_instruction": text,
                **locale_updates,
            }
        logger.info("quoted_replay_shortcut_miss", decision=interpretation.decision, reason=interpretation.reason)
    except Exception as exc:
        logger.warning("quoted_replay_shortcut_failed", error=str(exc))

    return None


__all__ = ["_handle_quoted_replay_shortcut"]
