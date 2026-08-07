"""Execution wave task guard helpers."""

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import (
    INPUT_MUTABLE_STAGES,
    TRANSACTION_TASK_TYPES,
    _build_mandate_gate_error,
    _dependency_resolution,
    _is_read_only_data_plan_query,
)
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.source_selection import (
    _is_same_batch_source_selection_sibling,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import (
    non_terminal_tasks,
    task_map,
    task_types_for_ids,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import cancel_task, fail_task
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from banking.accounts.mandate_state import is_mandate_debit_ready
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def _active_input_task_types(state: OrchestratorState) -> set[str]:
    interrupt = last_interrupt(state)
    if not interrupt.exists:
        return set()
    return task_types_for_ids(state, interrupt.task_ids)


def _should_defer_during_input_interrupt(
    *,
    state: OrchestratorState,
    task_id: str,
    task: TaskSpec,
    active_input_task_types: set[str],
) -> bool:
    interrupt = last_interrupt(state)
    if _is_same_batch_transaction_sibling(state, task_id):
        return False
    return bool(
        interrupt.is_kind("input")
        and interrupt.task_ids
        and not interrupt.includes_task(task_id)
        and task.type in active_input_task_types
        and task.stage in INPUT_MUTABLE_STAGES
        and not _is_same_batch_source_selection_sibling(state, task_id)
    )


def _is_same_batch_transaction_sibling(state: OrchestratorState, task_id: str) -> bool:
    interrupt = last_interrupt(state)
    if not interrupt.is_kind("input") or not interrupt.task_ids:
        return False

    task = task_map(state).get(task_id)
    if not task or task.type not in TRANSACTION_TASK_TYPES:
        return False

    # Focused batch-input interrupts keep the user-facing task_ids narrow,
    # but carry the complete live batch in metadata.  Those siblings must be
    # allowed to re-run with their preserved payloads after the focused slot
    # is answered.
    metadata = getattr(interrupt.interrupt, "metadata", None)
    raw_batch_scope = metadata.get("batch_task_ids") if isinstance(metadata, dict) else None
    if isinstance(raw_batch_scope, list) and task_id in {str(value) for value in raw_batch_scope}:
        return True

    group_id = task.payload.get("async_group_id")
    if not group_id:
        return False

    for active_task_id in interrupt.task_ids:
        if active_task_id == task_id:
            continue
        active_task = task_map(state).get(active_task_id)
        if (
            active_task
            and active_task.type in TRANSACTION_TASK_TYPES
            and active_task.payload.get("async_group_id") == group_id
        ):
            return True
    return False


def _apply_dependency_status(
    *,
    task: TaskSpec,
    task_id: str,
    state: OrchestratorState,
) -> bool | None:
    dep_status, dep_id = _dependency_resolution(task, task_map(state))
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

    context = loaded_context(state)
    accounts = context.transaction_accounts
    ready_transaction_count = sum(
        1 for account in accounts if isinstance(account, dict) and is_mandate_debit_ready(account)
    )
    has_ready = ready_transaction_count > 0
    all_accounts = context.account_rows
    ready_all_account_count = sum(
        1 for account in all_accounts if isinstance(account, dict) and is_mandate_debit_ready(account)
    )
    log_orchestrator_diagnostic(
        logger,
        "mandate_gate_evaluated",
        task_id=task.id,
        task_type=task.type,
        typed_self_signal=task.payload.get("is_self") is True,
        transaction_account_count=len(accounts),
        transaction_ready_count=ready_transaction_count,
        all_account_count=len(all_accounts),
        all_ready_count=ready_all_account_count,
    )
    if not has_ready:
        # ``transaction_accounts`` is a derived, source-eligible view and may
        # lag the authoritative linked-account snapshot after a reseed/link.
        # Do not reject the entire batch on that stale projection.  The
        # worker will still validate the selected source and refresh a typed
        # self-transfer destination before resolution.
        has_ready = ready_all_account_count > 0
        if has_ready:
            log_orchestrator_diagnostic(
                logger,
                "mandate_gate_full_account_fallback",
                task_id=task.id,
                task_type=task.type,
                typed_self_signal=task.payload.get("is_self") is True,
                transaction_account_count=len(accounts),
                all_account_count=len(all_accounts),
                all_ready_count=ready_all_account_count,
            )
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
    pending_tasks = non_terminal_tasks(state, current_wave)
    pending = [task_id for task_id, _task in pending_tasks]
    if not pending:
        return

    logger.warning("dependency_deadlock_wave_cancelled", wave=current_wave, pending_tasks=pending)
    for _task_id, task in pending_tasks:
        cancel_task(task, "unresolved dependency deadlock")


__all__ = [
    "_active_input_task_types",
    "_apply_dependency_status",
    "_apply_mandate_gate_failure",
    "_cancel_deadlocked_wave_tasks",
    "_is_same_batch_transaction_sibling",
    "_should_defer_during_input_interrupt",
]
