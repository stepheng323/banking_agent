"""Context-frame shortcut handling for planner context assembly."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_flow_types import PlannerContextBuildResult
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state as build_context_frame_followup_context_for_state,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from apps.chat.src.agent.workers.query.services.reasoning.shortcuts import resolve_query_shortcut
from shared.services.task_planner import TaskPlanner
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def try_context_frame_followup_shortcut(
    *,
    state: OrchestratorState,
    text: str,
    locale_updates: dict[str, Any],
    task_planner: TaskPlanner | None,
) -> PlannerContextBuildResult | None:
    frame = OrchestratorContextManager().latest_active_frame(state)
    if not frame or task_planner is None:
        return None

    shortcut = resolve_query_shortcut(text, state.loaded_context.get("language"))
    if shortcut is not None and shortcut.kind == "pagination":
        logger.info(
            "context_frame_followup_skipped_for_query_pagination",
            action=shortcut.action,
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None

    try:
        decision = await task_planner.interpret_context_frame_followup(
            state.phone_number,
            text,
            context=build_context_frame_followup_context_for_state(state),
            path_label="planner_path",
        )
    except Exception as exc:
        logger.warning("context_frame_followup_interpreter_failed", error=str(exc))
        return None

    frame_followup = build_context_frame_followup_response(state, text, decision=decision)
    logger.info(
        "context_frame_followup_decision",
        decision=decision.decision,
        confidence=decision.confidence,
        detected_language=decision.detected_language,
        requested_field=decision.requested_field,
        rank=decision.rank,
        has_filters=bool(decision.filters),
        reason=decision.reason,
        resolved=bool(frame_followup),
    )
    replay_modifier = None
    if decision.decision in {"replay_tasks", "replay"}:
        try:
            replay_modifier = await task_planner.extract_context_frame_replay_modifiers(
                state.phone_number,
                text,
                context=build_context_frame_followup_context_for_state(state),
                path_label="planner_path",
            )
        except Exception as exc:
            logger.warning("context_frame_replay_modifier_extractor_failed", error=str(exc))
        else:
            if replay_modifier is not None:
                logger.info(
                    "context_frame_replay_modifier_extracted",
                    confidence=replay_modifier.confidence,
                    detected_language=replay_modifier.detected_language,
                    has_amount=replay_modifier.amount is not None,
                    has_source=bool(replay_modifier.source_account_reference),
                    has_narration=bool(replay_modifier.narration),
                    reason=replay_modifier.reason,
                )
                frame_followup = build_context_frame_followup_response(
                    state,
                    text,
                    decision=decision,
                    replay_modifier=replay_modifier,
                )
    if not frame_followup:
        return None

    logger.info(
        "context_frame_followup_hit",
        frame_type=frame.frame_type.value,
        item_count=len(frame.items),
    )
    return PlannerContextBuildResult(
        planner_context="None",
        active_intent=None,
        query_session_snapshot=None,
        query_session_source=None,
        prompt_signals=PlannerPromptSignals(
            active_flow_type=None,
            pending_interrupt_kind=None,
            query_session_active=False,
            query_session_source=None,
            recent_domain_focus=frame_followup.recent_domain_focus,
            has_beneficiary_suggestion=False,
            has_user_state_summary=False,
            has_short_term_memory=True,
            has_quote=False,
            has_transaction_intent_hint=False,
            forced_domain_owner=None,
            expected_transaction_executors=(),
        ),
        shortcut_updates={
            "semantic_path_shape": frame_followup.semantic_path_shape,
            "context_frames": frame_followup.context_frames or state.context_frames,
            **({"final_response": frame_followup.response} if frame_followup.response else {}),
            **({"tasks": frame_followup.tasks} if frame_followup.tasks else {}),
            **({"waves": frame_followup.waves} if frame_followup.waves else {}),
            **({"current_wave_index": 0} if frame_followup.waves else {}),
            **locale_updates,
        },
    )


__all__ = ["try_context_frame_followup_shortcut"]
