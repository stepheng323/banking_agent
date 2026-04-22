"""Planner quoted replay shortcut flow helpers."""

import re
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner.quoted_replay import (
    _build_deterministic_quoted_replay_updates,
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
QUOTED_REPLAY_ACTION_PATTERNS = (
    r"\b(again|retry|resend|repeat|same)\b",
    r"\b(send|buy|do|run|process)\b",
)


def _should_attempt_quoted_replay(text: str, quoted_payload: dict[str, Any] | None) -> bool:
    if not quoted_payload:
        return False
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    # Short quote replies are usually actionable ("again", "send now", "do same").
    if len(normalized) <= 24:
        return True
    return any(re.search(pattern, normalized) for pattern in QUOTED_REPLAY_ACTION_PATTERNS)


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
    if not (state.has_quote and state.quoted_message_id):
        return None

    quoted_payload = await _load_quoted_actionable_payload(state, config)
    deterministic_updates = _build_deterministic_quoted_replay_updates(
        state=state,
        text=text,
        quoted_payload=quoted_payload,
        locale_updates=locale_updates,
    )
    if deterministic_updates is not None:
        return deterministic_updates

    if not hasattr(task_planner, "interpret_quoted_replay"):
        return None

    if not _should_attempt_quoted_replay(text, quoted_payload):
        logger.info("quoted_replay_preconditions_not_met", has_payload=bool(quoted_payload))
        return None

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
                "semantic_path_shape": "quoted_router",
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
                    "semantic_path_shape": "quoted_router",
                    **locale_updates,
                }
            if quoted_payload is None:
                logger.info("quoted_replay_actionable_payload_missing", quoted_message_id=state.quoted_message_id)
                return {
                    "final_response": render_message("conversational.clarify", current_locale),
                    "normalized_instruction": text,
                    "semantic_path_shape": "quoted_router",
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
                "semantic_path_shape": "quoted_router",
                **locale_updates,
            }
        logger.info("quoted_replay_shortcut_miss", decision=interpretation.decision, reason=interpretation.reason)
    except Exception as exc:
        logger.warning("quoted_replay_shortcut_failed", error=str(exc))

    return None


__all__ = ["_handle_quoted_replay_shortcut"]
