"""Execution wave task guard helpers."""

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import (
    INPUT_MUTABLE_STAGES,
    TERMINAL_STAGES,
    TRANSACTION_TASK_TYPES,
    _build_mandate_gate_error,
    _dependency_resolution,
    _is_read_only_data_plan_query,
)
from apps.chat.src.agent.orchestrator.workflows.execution.source_selection import (
    _is_same_batch_source_selection_sibling,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import cancel_task, fail_task
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from banking.accounts.mandate_state import is_mandate_debit_ready
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _active_input_task_types(state: OrchestratorState) -> set[str]:
    if not state.last_interrupt:
        return set()
    return {
        state.tasks[active_task_id].type
        for active_task_id in state.last_interrupt.task_ids
        if active_task_id in state.tasks
    }


def _should_defer_during_input_interrupt(
    *,
    state: OrchestratorState,
    task_id: str,
    task: TaskSpec,
    active_input_task_types: set[str],
) -> bool:
    return bool(
        state.last_interrupt
        and state.last_interrupt.kind == "input"
        and state.last_interrupt.task_ids
        and task_id not in state.last_interrupt.task_ids
        and task.type in active_input_task_types
        and task.stage in INPUT_MUTABLE_STAGES
        and not _is_same_batch_source_selection_sibling(state, task_id)
    )


def _apply_dependency_status(
    *,
    task: TaskSpec,
    task_id: str,
    state: OrchestratorState,
) -> bool | None:
    dep_status, dep_id = _dependency_resolution(task, state.tasks)
    if dep_status == "cancel":
        cancel_task(task, f"dependency {dep_id} not successful")
        logger.info("task_cancelled_by_dependency", task_id=task_id, dependency=dep_id)
        return True
    if dep_status == "wait":
        logger.info("task_waiting_for_dependency", task_id=task_id, dependency=dep_id)
        return None
    return False


def _apply_mandate_gate_failure(
    *,
    state: OrchestratorState,
    task: TaskSpec,
    runtime: ExecutionWaveRuntime,
) -> bool:
    if task.type not in TRANSACTION_TASK_TYPES or _is_read_only_data_plan_query(task):
        return False

    accounts = (state.loaded_context or {}).get("transaction_accounts") or []
    has_ready = any(isinstance(account, dict) and is_mandate_debit_ready(account) for account in accounts)
    if has_ready:
        return False

    fail_task(
        task,
        _build_mandate_gate_error(runtime.mandate_gate_accounts or accounts, runtime.locale),
        {
            "is_pending_mandate": True,
            "mandate_accounts": runtime.mandate_gate_accounts,
        },
    )
    return True


def _cancel_deadlocked_wave_tasks(
    *,
    state: OrchestratorState,
    current_wave: list[str],
) -> None:
    pending = [
        task_id
        for task_id in current_wave
        if (task := state.tasks.get(task_id)) is not None and task.stage not in TERMINAL_STAGES
    ]
    if not pending:
        return

    logger.warning("dependency_deadlock_wave_cancelled", wave=current_wave, pending_tasks=pending)
    for task_id in pending:
        cancel_task(state.tasks[task_id], "unresolved dependency deadlock")


__all__ = [
    "_active_input_task_types",
    "_apply_dependency_status",
    "_apply_mandate_gate_failure",
    "_cancel_deadlocked_wave_tasks",
    "_should_defer_during_input_interrupt",
]
