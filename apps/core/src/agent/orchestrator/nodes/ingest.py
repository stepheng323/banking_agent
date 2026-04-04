from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)
SESSION_TZ = ZoneInfo("Africa/Lagos")
_TERMINAL_TASK_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


def _current_session_date() -> str:
    return datetime.now(SESSION_TZ).date().isoformat()


def _all_tasks_terminal(state: OrchestratorState) -> bool:
    if not state.tasks:
        return False
    return all(task.stage in _TERMINAL_TASK_STAGES for task in state.tasks.values())


async def ingest_message(state: OrchestratorState) -> dict[str, Any]:
    """Entry point. Setup state for the new turn."""
    logger.info("ingest_message", user=state.phone_number, text=state.last_message_text)

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

    if state.last_activity_date and state.last_activity_date != today:
        logger.info(
            "ingest_day_rollover_reset",
            phone_number=state.phone_number,
            previous=state.last_activity_date,
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

    if _all_tasks_terminal(state) and state.pending_interrupt is None:
        logger.info(
            "ingest_terminal_state_reset",
            phone_number=state.phone_number,
            task_count=len(state.tasks),
            wave_count=len(state.waves),
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

    if state.last_callback:
        logger.info("processing_callback", payload=state.last_callback)
        updates["last_message_text"] = None
        updates["last_message_id"] = None
        if state.last_callback.get("pin_verified"):
            updates["pin_verified"] = True

    return updates
