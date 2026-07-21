"""Planner context assembly flow helpers."""

from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_mode_decisions import (
    _should_use_minimal_planner_context,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_sections import (
    build_planner_context_sections,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_state import build_context_flow_state
from apps.chat.src.agent.orchestrator.workflows.planner.context.flow.context_flow_types import PlannerContextBundle
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_core import (
    PLANNER_CONTEXT_MAX_CHARS,
    PLANNER_CONTEXT_SECTION_SEPARATOR,
    _assemble_planner_context,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary import (
    get_or_build_turn_context_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import planner_state_view
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_planner_context(
    *,
    state: OrchestratorState,
    text: str,
    redis_client: redis.Redis | None,
    locale_updates: dict[str, Any],
    task_planner: TaskPlanner | None = None,
) -> PlannerContextBundle:
    state_view = planner_state_view(state)
    flow_state = await build_context_flow_state(state_view=state_view, text=text, redis_client=redis_client)
    if _should_use_minimal_planner_context(
        state_view=state_view,
        active_intent=flow_state.active_intent,
        query_session_active=flow_state.query_session_active,
        recent_domain_focus=flow_state.recent_domain_focus,
        recent_answer_focus=flow_state.recent_answer_focus,
        has_transaction_intent_hint=flow_state.has_transaction_intent_hint,
    ):
        logger.info("planner_context_skipped", mode="minimal")
        return PlannerContextBundle(
            planner_context="None",
            active_intent=flow_state.active_intent,
            query_session_snapshot=flow_state.query_session_snapshot,
            query_session_source=flow_state.query_session_source,
            prompt_signals=PlannerPromptSignals(
                active_flow_type=flow_state.active_intent,
                pending_interrupt_kind=None,
                query_session_active=False,
                query_session_source=flow_state.query_session_source,
                recent_domain_focus=None,
                has_beneficiary_suggestion=False,
                has_user_state_summary=False,
                has_short_term_memory=False,
                has_quote=False,
                has_transaction_intent_hint=flow_state.has_transaction_intent_hint,
                compact_context=True,
                forced_domain_owner=flow_state.forced_domain_owner,
                expected_transaction_executors=flow_state.expected_executors,
            ),
        )

    turn_summary, _ = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=flow_state.query_session_snapshot,
        query_session_source=flow_state.query_session_source,
        path_label="planner_path",
    )
    section_result = build_planner_context_sections(
        turn_summary=turn_summary,
        query_session_source=flow_state.query_session_source,
        is_transactional_flow=flow_state.is_transactional_flow,
        active_intent=flow_state.active_intent,
        compact_transaction_context=flow_state.compact_transaction_context,
    )

    raw_chars = sum(len(content) for _, content in section_result.sections)
    if len(section_result.sections) > 1:
        raw_chars += len(PLANNER_CONTEXT_SECTION_SEPARATOR) * (len(section_result.sections) - 1)
    planner_context, included_sections, clipped_sections, dropped_sections = _assemble_planner_context(
        section_result.sections,
        max_chars=PLANNER_CONTEXT_MAX_CHARS,
    )
    logger.info(
        "planner_context_size",
        chars=len(planner_context),
        raw_chars=raw_chars,
        truncated=bool(clipped_sections),
        sections=len(included_sections),
        included_sections=included_sections,
        clipped_sections=clipped_sections,
        dropped_sections=dropped_sections,
    )
    prompt_signals = PlannerPromptSignals(
        active_flow_type=flow_state.active_intent,
        pending_interrupt_kind=state_view.pending_interrupt_kind,
        query_session_active=flow_state.query_session_active,
        query_session_source=flow_state.query_session_source,
        recent_domain_focus=None if flow_state.compact_transaction_context else section_result.recent_domain_focus,
        has_beneficiary_suggestion=False,
        has_user_state_summary=section_result.has_user_state_summary,
        has_short_term_memory=section_result.has_short_term_memory,
        has_quote=state_view.has_quoted_message,
        has_transaction_intent_hint=flow_state.has_transaction_intent_hint,
        compact_context=flow_state.compact_transaction_context,
        forced_domain_owner=flow_state.forced_domain_owner,
        expected_transaction_executors=flow_state.expected_executors,
    )

    return PlannerContextBundle(
        planner_context=planner_context,
        active_intent=flow_state.active_intent,
        query_session_snapshot=flow_state.query_session_snapshot,
        query_session_source=flow_state.query_session_source,
        prompt_signals=prompt_signals,
    )


__all__ = [
    "PlannerContextBundle",
    "_build_planner_context",
]
