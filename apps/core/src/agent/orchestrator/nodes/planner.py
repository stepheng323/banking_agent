from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    CONTEXT_ACCOUNT_PREVIEW_LIMIT,
    PLANNER_CONTEXT_MAX_CHARS,
    _assemble_planner_context,
    _build_query_session_context,
    _build_user_state_summary,
)
from apps.core.src.agent.orchestrator.nodes.planner_context_flow import _build_planner_context
from apps.core.src.agent.orchestrator.nodes.planner_execution_flow import _execute_planner_with_context
from apps.core.src.agent.orchestrator.nodes.planner_guardrails import (
    _deescalate_mandate_acknowledgement,
    _filter_spurious_affirmation_tasks,
)
from apps.core.src.agent.orchestrator.nodes.planner_policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _build_policy_notice,
    _detected_locale_value,
    _meta_intent_from_response_key,
)
from apps.core.src.agent.orchestrator.nodes.planner_postprocess import _should_replan_active_wave
from apps.core.src.agent.orchestrator.nodes.planner_quoted_flow import _handle_quoted_replay_shortcut
from apps.core.src.agent.orchestrator.nodes.planner_quoted_replay import (
    QUOTED_REPLAY_MIN_CONFIDENCE as _QUOTED_REPLAY_MIN_CONFIDENCE,
)
from apps.core.src.agent.orchestrator.nodes.planner_response_flow import _build_non_task_response
from apps.core.src.agent.orchestrator.nodes.planner_task_flow import _build_planner_task_updates
from shared.i18n import LocaleManager, render_safe_capability_fallback
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SAFE_CAPABILITY_FALLBACK = render_safe_capability_fallback("en")
QUOTED_REPLAY_MIN_CONFIDENCE = _QUOTED_REPLAY_MIN_CONFIDENCE


async def plan_tasks(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Planner Node.

    1. If new request (no active waves), call Planner to create TaskSpecs.
    2. If existing waves, this is a pass-through (or bulk extraction update).
    """
    if state.waves and state.pending_interrupt is None and not _should_replan_active_wave(state):
        return {}

    task_planner = config["configurable"].get("task_planner")
    text = state.last_message_text or ""
    current_locale = LocaleManager.normalize(state.loaded_context.get("language")).value
    redis_client = config["configurable"].get("redis_client")
    if task_planner is None:
        logger.error("task_planner_missing")
        return {"final_response": render_safe_capability_fallback(current_locale)}

    locale_updates = _build_locale_update(state, current_locale)

    quoted_replay_updates = await _handle_quoted_replay_shortcut(
        state=state,
        config=config,
        task_planner=task_planner,
        text=text,
        current_locale=current_locale,
        locale_updates=locale_updates,
        quoted_replay_min_confidence=QUOTED_REPLAY_MIN_CONFIDENCE,
    )
    if quoted_replay_updates is not None:
        return quoted_replay_updates

    context_result = await _build_planner_context(
        state=state,
        text=text,
        redis_client=redis_client,
        locale_updates=locale_updates,
    )
    if context_result.shortcut_updates is not None:
        return context_result.shortcut_updates

    planner_context = context_result.planner_context
    active_intent = context_result.active_intent
    query_session_snapshot = context_result.query_session_snapshot
    query_session_source = context_result.query_session_source

    try:
        execution_result = await _execute_planner_with_context(
            state=state,
            task_planner=task_planner,
            text=text,
            planner_context=planner_context,
            active_intent=active_intent,
            current_locale=current_locale,
            redis_client=redis_client,
        )
    except Exception as e:
        logger.error("planner_failed", error=str(e))
        return {}

    planner_output = execution_result.planner_output
    current_locale = execution_result.current_locale
    fastpath_context_updates = execution_result.fastpath_context_updates

    handled_response = await _build_non_task_response(
        state=state,
        planner_output=planner_output,
        text=text,
        task_planner=task_planner,
        redis_client=redis_client,
        active_intent=active_intent,
        current_locale=current_locale,
        locale_updates=locale_updates,
        fastpath_context_updates=fastpath_context_updates,
    )
    if handled_response is not None:
        return handled_response

    if state.waves and active_intent:
        logger.info("planner_intent_switch_or_update", old=active_intent, new=planner_output.primary_intent)

    task_updates = await _build_planner_task_updates(
        state=state,
        task_planner=task_planner,
        planner_output=planner_output,
        text=text,
        planner_context=planner_context,
        active_intent=active_intent,
        current_locale=current_locale,
        query_session_source=query_session_source,
        query_session_snapshot=query_session_snapshot,
    )
    planner_output = task_updates["planner_output"]
    new_tasks = task_updates["new_tasks"]
    waves = task_updates["waves"]
    stashed_query_session_update = task_updates["stashed_query_session_update"]

    policy_notice = _build_policy_notice(text, planner_output, current_locale)
    if policy_notice:
        logger.info("policy_notice_created")

    return {
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": planner_output,
        "policy_notice": policy_notice,
        "stashed_query_session": (
            stashed_query_session_update if stashed_query_session_update else state.stashed_query_session
        ),
        **locale_updates,
    }


__all__ = [
    "CONTEXT_ACCOUNT_PREVIEW_LIMIT",
    "PLANNER_CONTEXT_MAX_CHARS",
    "QUOTED_REPLAY_MIN_CONFIDENCE",
    "SAFE_CAPABILITY_FALLBACK",
    "_assemble_planner_context",
    "_build_policy_aware_greeting",
    "_build_policy_notice",
    "_build_query_session_context",
    "_build_user_state_summary",
    "_deescalate_mandate_acknowledgement",
    "_detected_locale_value",
    "_filter_spurious_affirmation_tasks",
    "_meta_intent_from_response_key",
    "plan_tasks",
]
