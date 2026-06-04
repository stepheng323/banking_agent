"""Planner quoted replay shortcut flow helpers."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_context import (
    _build_quoted_replay_context,
    _build_quoted_replay_context_with_payload,
    _load_quoted_actionable_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_payload_response import (
    _quoted_replay_clarify_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_payload_updates import (
    _build_quoted_replay_execution_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.intent.routing_signals import looks_like_transaction_replay_modifier_request
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.presentation.i18n.renderer import render_message
from shared.types.planner import ContextFrameReplayModifier
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def _handle_quoted_replay_shortcut(
    *,
    state_view: PlannerStateView,
    actionable_message_repo: ActionableMessageRepository | None,
    task_planner: TaskPlanner | None,
    text: str,
    current_locale: str,
    locale_updates: dict[str, Any],
    quoted_replay_min_confidence: float,
) -> dict[str, Any] | None:
    if not state_view.has_quoted_message:
        return None

    quoted_payload = await _load_quoted_actionable_payload(state_view, actionable_message_repo)

    if task_planner is None:
        return None

    quoted_context = (
        _build_quoted_replay_context_with_payload(state_view, quoted_payload)
        if quoted_payload is not None
        else _build_quoted_replay_context(state_view)
    )
    try:
        interpretation = cast(
            QuotedReplayInterpretation,
            await task_planner.interpret_quoted_replay(state_view.phone_number, text, context=quoted_context),
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
                logger.info(
                    "quoted_replay_actionable_payload_missing",
                    quoted_message_id_hash=log_fingerprint(state_view.quoted_message_id),
                )
                return {
                    "final_response": render_message("conversational.clarify", current_locale),
                    "normalized_instruction": text,
                    "semantic_path_shape": "quoted_router",
                    **locale_updates,
                }
            replay_modifier: ContextFrameReplayModifier | None = None
            if looks_like_transaction_replay_modifier_request(text):
                try:
                    replay_modifier = cast(
                        ContextFrameReplayModifier | None,
                        await task_planner.extract_context_frame_replay_modifiers(
                            state_view.phone_number,
                            text,
                            context=quoted_context,
                            path_label="planner_path",
                        ),
                    )
                except Exception as exc:
                    logger.warning("quoted_replay_modifier_extractor_failed", error=str(exc))
                else:
                    if replay_modifier is not None:
                        logger.info(
                            "quoted_replay_modifier_extracted",
                            confidence=replay_modifier.confidence,
                            detected_language=replay_modifier.detected_language,
                            has_amount=replay_modifier.amount is not None,
                            has_source=bool(replay_modifier.source_account_reference),
                            has_narration=bool(replay_modifier.narration),
                            reason=replay_modifier.reason,
                        )
            replay_updates = _build_quoted_replay_execution_updates(
                state_view=state_view,
                text=text,
                interpretation=interpretation,
                locale_updates=locale_updates,
                quoted_payload=quoted_payload,
                replay_modifier=replay_modifier,
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
