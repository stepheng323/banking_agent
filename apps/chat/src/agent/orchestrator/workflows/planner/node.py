from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_flow import _build_planner_context
from apps.chat.src.agent.orchestrator.workflows.planner.execution_flow import _execute_planner_with_context
from apps.chat.src.agent.orchestrator.workflows.planner.node_constants import QUOTED_REPLAY_MIN_CONFIDENCE
from apps.chat.src.agent.orchestrator.workflows.planner.node_recovery import _apply_planner_recovery
from apps.chat.src.agent.orchestrator.workflows.planner.node_responses import (
    _context_read_shortcut_response,
    _non_task_route_response,
    _planner_failed_response,
    _planner_unavailable_response,
    _quoted_replay_route_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.node_task_response import _build_planner_task_response
from apps.chat.src.agent.orchestrator.workflows.planner.policy.policy_locale import _build_locale_update
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_flow import _should_replan_active_wave
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_flow import _handle_quoted_replay_shortcut
from apps.chat.src.agent.orchestrator.workflows.planner.response.non_task_response import _build_non_task_response
from apps.chat.src.agent.orchestrator.workflows.planner.runtime import build_planner_runtime
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_build import _build_planner_task_updates
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def plan_tasks(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Planner Node.

    1. If new request (no active waves), call Planner to create TaskSpecs.
    2. If existing waves, this is a pass-through (or bulk extraction update).
    """
    if state.waves and state.pending_interrupt is None and not _should_replan_active_wave(state):
        return {}

    runtime = build_planner_runtime(state, config)
    dependencies = runtime.dependencies
    task_planner = dependencies.task_planner
    text = runtime.text
    current_locale = runtime.current_locale
    redis_client = dependencies.redis_client
    if task_planner is None:
        logger.error("task_planner_missing")
        return _planner_unavailable_response(current_locale)

    locale_updates = _build_locale_update(state, current_locale)

    quoted_replay_updates = await _handle_quoted_replay_shortcut(
        state=state,
        actionable_message_repo=dependencies.actionable_message_repo,
        task_planner=task_planner,
        text=text,
        current_locale=current_locale,
        locale_updates=locale_updates,
        quoted_replay_min_confidence=QUOTED_REPLAY_MIN_CONFIDENCE,
    )
    if quoted_replay_updates is not None:
        return _quoted_replay_route_response(quoted_replay_updates)

    context_result = await _build_planner_context(
        state=state,
        text=text,
        redis_client=redis_client,
        locale_updates=locale_updates,
        task_planner=task_planner,
    )
    if context_result.shortcut_updates is not None:
        return _context_read_shortcut_response(context_result.shortcut_updates)

    planner_context = context_result.planner_context
    active_intent = context_result.active_intent
    query_session_snapshot = context_result.query_session_snapshot
    query_session_source = context_result.query_session_source
    prompt_signals = context_result.prompt_signals

    try:
        execution_result = await _execute_planner_with_context(
            state=state,
            task_planner=task_planner,
            text=text,
            planner_context=planner_context,
            prompt_signals=prompt_signals,
            active_intent=active_intent,
            current_locale=current_locale,
            redis_client=redis_client,
        )
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return _planner_failed_response()

    planner_output = execution_result.planner_output
    current_locale = execution_result.current_locale
    context_read_updates = execution_result.context_read_updates

    locale_updates = _build_locale_update(state, current_locale)

    _apply_planner_recovery(planner_output, text, active_session_present=bool(state.session_stack))

    handled_response = await _build_non_task_response(
        state=state,
        planner_output=planner_output,
        text=text,
        redis_client=redis_client,
        active_intent=active_intent,
        current_locale=current_locale,
        locale_updates=locale_updates,
        context_read_updates=context_read_updates,
        conversation_responder=dependencies.conversation_responder,
    )
    if handled_response is not None:
        return _non_task_route_response(handled_response=handled_response, planner_output=planner_output)

    if state.waves and active_intent:
        logger.info("planner_intent_switch_or_update", old=active_intent, new=planner_output.primary_intent)

    task_updates = await _build_planner_task_updates(
        planner_output=planner_output,
        text=text,
        locale=current_locale,
        query_session_source=query_session_source,
        query_session_snapshot=query_session_snapshot,
    )
    planner_output = task_updates["planner_output"]
    return _build_planner_task_response(
        state=state,
        task_updates=task_updates,
        planner_output=planner_output,
        text=text,
        current_locale=current_locale,
        locale_updates=locale_updates,
    )


__all__ = ["plan_tasks"]
