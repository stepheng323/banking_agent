"""Planner-switch update builder for interrupt routes."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import (
    _clear_current_domain_sessions,
    _is_resumable_interrupt,
    logger,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_session_stash import _stash_current_session


def _build_planner_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    text: str,
    expected_executors: list[str],
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": None,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if expected_executors:
        updates["preplanner_expected_transaction_executors"] = expected_executors

    if current_task_types.issubset(TRANSACTION_INTENTS) and _is_resumable_interrupt(interrupt):
        stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
        updates["stashed_sessions"] = stashed
        updates["referent_memory"] = interrupt_state_view(state).referent_memory
        logger.info(
            "interrupt_switch_to_planner_stashed",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    else:
        logger.info(
            "interrupt_switch_to_planner_replaced",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    return updates


__all__ = ["_build_planner_switch_updates"]
