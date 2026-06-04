from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.state_view import (
    LifecycleStateView,
    lifecycle_state_view,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)
SESSION_TZ = ZoneInfo("Africa/Lagos")


def _current_session_date() -> str:
    return datetime.now(SESSION_TZ).date().isoformat()


def _all_tasks_terminal(state_view: LifecycleStateView) -> bool:
    return state_view.all_tasks_terminal


def _has_unblocked_nonterminal_wave(state_view: LifecycleStateView) -> bool:
    return state_view.has_unblocked_nonterminal_wave


async def ingest_message(state: OrchestratorState) -> dict[str, Any]:
    """Entry point. Setup state for the new turn."""
    state_view = lifecycle_state_view(state)
    logger.info("ingest_message", user=state_view.phone_number, text=state_view.last_message_text)

    today = _current_session_date()
    updates: dict[str, Any] = {
        "outbox": [],
        "final_response": None,
        "policy_notice": None,
        "direct_path_triggered": False,
        "preplanner_expected_transaction_executors": [],
        "last_interrupt": None,
        "last_activity_date": today,
        "turn_context_summary": None,
        "semantic_path_shape": None,
        "routing_owner": None,
        "routing_decision": None,
        "routing_target_domain": None,
        "routing_mode": None,
        "planner_used": False,
    }

    if state_view.last_activity_date and state_view.last_activity_date != today:
        logger.info(
            "ingest_day_rollover_reset",
            phone_number=state_view.phone_number,
            previous=state_view.last_activity_date,
            current=today,
        )
        updates.update(
            {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "task_results": {},
                "planner_output": None,
                "normalized_instruction": None,
                "pending_interrupt": None,
                "pin_verified": False,
                "session_stack": [],
                "active_domain": None,
                "stashed_sessions": [],
                "stashed_query_session": None,
            }
        )

    if _all_tasks_terminal(state_view) and not state_view.has_pending_interrupt:
        logger.info(
            "ingest_terminal_state_reset",
            phone_number=state_view.phone_number,
            task_count=state_view.task_count,
            wave_count=state_view.wave_count,
        )
        updates.update(
            {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "task_results": {},
                "planner_output": None,
                "normalized_instruction": None,
                "session_stack": [],
                "active_domain": None,
                "pin_verified": False,
            }
        )
    elif _has_unblocked_nonterminal_wave(state_view):
        logger.warning(
            "ingest_unblocked_nonterminal_state_reset",
            phone_number=state_view.phone_number,
            current_wave_index=state_view.current_wave_index,
            wave_count=state_view.wave_count,
            task_shapes=state_view.active_nonterminal_wave_task_shapes,
        )
        updates.update(
            {
                "tasks": {},
                "waves": [],
                "current_wave_index": 0,
                "task_results": {},
                "planner_output": None,
                "normalized_instruction": None,
                "session_stack": [],
                "active_domain": None,
                "pin_verified": False,
            }
        )

    if state_view.has_last_callback:
        logger.info("processing_callback", payload=state_view.last_callback)
        updates["last_message_text"] = None
        updates["last_message_id"] = None
        if state_view.last_callback_pin_verified:
            updates["pin_verified"] = True

    return updates
