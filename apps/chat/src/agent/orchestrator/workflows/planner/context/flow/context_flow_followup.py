"""Context-frame shortcut handling for planner context assembly."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_types import PlannerContextBundle
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state_view as build_context_frame_followup_context_for_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_response_for_state_view as build_context_frame_followup_response_for_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_state_view import (
    context_frame_state_view,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from banking.transactions.query.services.reasoning.shortcuts import resolve_query_shortcut
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def try_context_frame_followup_shortcut(
    *,
    state_view: PlannerStateView,
    text: str,
    locale_updates: dict[str, Any],
    task_planner: TaskPlanner | None,
) -> PlannerContextBundle | None:
    frame_state_view = context_frame_state_view(state_view.state)
    frame = frame_state_view.latest_active_frame()
    if not frame or task_planner is None:
        return None

    shortcut = resolve_query_shortcut(text, state_view.current_locale)
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
            state_view.phone_number,
            text,
            context=build_context_frame_followup_context_for_state_view(frame_state_view),
            path_label="planner_path",
        )
    except Exception as exc:
        logger.warning("context_frame_followup_interpreter_failed", error=str(exc))
        return None

    frame_followup = build_context_frame_followup_response_for_state_view(frame_state_view, text, decision=decision)
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
                state_view.phone_number,
                text,
                context=build_context_frame_followup_context_for_state_view(frame_state_view),
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
                frame_followup = build_context_frame_followup_response_for_state_view(
                    frame_state_view,
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
    return PlannerContextBundle(
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
            "context_frames": frame_followup.context_frames or state_view.context_frames,
            **({"final_response": frame_followup.response} if frame_followup.response else {}),
            **({"tasks": frame_followup.tasks} if frame_followup.tasks else {}),
            **({"waves": frame_followup.waves} if frame_followup.waves else {}),
            **({"current_wave_index": 0} if frame_followup.waves else {}),
            **locale_updates,
        },
    )


__all__ = ["try_context_frame_followup_shortcut"]
