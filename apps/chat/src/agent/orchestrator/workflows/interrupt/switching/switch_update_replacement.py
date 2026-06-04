"""Replacement and stash switch update builders."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import _clear_current_domain_sessions, logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_session_stash import _stash_current_session
from shared.types.planner import PlannerOutput


def _build_stash_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    state_view = interrupt_state_view(state)

    logger.info(
        "interrupt_replan_switched",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        stashed=True,
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "stashed_sessions": stashed,
        "referent_memory": state_view.referent_memory,
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


def _build_replace_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    logger.info(
        "interrupt_replan_replaced",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


def _build_transaction_replacement_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    cleaned_stack = interrupt_state_view(state).session_stack_without_domains(TRANSACTION_INTENTS)
    logger.info(
        "interrupt_transaction_replaced",
        kind=interrupt.kind,
        cancelled_task_ids=interrupt.task_ids,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


__all__ = [
    "_build_replace_switch_updates",
    "_build_stash_switch_updates",
    "_build_transaction_replacement_updates",
]
