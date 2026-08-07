from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TRANSACTION_TASK_TYPES
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.source_selection import (
    _propagate_batch_source_selection,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import get_task, non_terminal_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import (
    fail_task,
    remove_task_payload_values,
    set_task_confirmation,
    set_task_stage,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_task_guards import (
    _active_input_task_types,
    _apply_dependency_status,
    _apply_mandate_gate_failure,
    _cancel_deadlocked_wave_tasks,
    _should_defer_during_input_interrupt,
)
from banking.runtime.operations import DEFAULT_OPERATION_BY_EXECUTOR, operation_spec
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _promote_deferred_confirmations(*, state: OrchestratorState, runtime: ExecutionWaveRuntime) -> None:
    """Promote private batch candidates once no task still needs input.

    A worker may reach confirmation before a sibling has resolved a recipient.
    Keeping that snapshot private avoids a partial review, while retaining it
    lets the next selection turn assemble the whole batch without replaying a
    completed leg.  Promotion is deliberately blocked while any input
    request remains in the accumulator.
    """
    if runtime.accumulator.input_request_count() > 0:
        return

    for task_id in runtime.current_wave:
        task = get_task(state, task_id)
        if task is None or task.stage in {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}:
            continue
        deferred = task.payload.get("_deferred_confirmation")
        if not isinstance(deferred, dict):
            continue
        outcome = str(deferred.get("outcome") or "")
        if outcome not in {"needs_confirmation", "needs_auth"}:
            remove_task_payload_values(task, "_deferred_confirmation")
            continue
        set_task_stage(task, TaskStage.AWAITING_AUTH if outcome == "needs_auth" else TaskStage.AWAITING_CONFIRMATION)
        set_task_confirmation(
            task,
            summary=deferred.get("summary"),
            snapshot=deferred.get("snapshot"),
            update_message=deferred.get("update_message"),
        )
        remove_task_payload_values(task, "_deferred_confirmation")
        if outcome == "needs_auth":
            runtime.accumulator.add_auth_task(task_id)
        else:
            runtime.accumulator.add_confirmation_task(task_id)
        logger.info("batch_confirmation_candidate_promoted", task_id=task_id, outcome=outcome)


async def execute_current_wave_tasks(
    *,
    state: OrchestratorState,
    runtime: ExecutionWaveRuntime,
) -> None:
    progressed = False
    interrupt = last_interrupt(state)
    active_input_task_types = _active_input_task_types(state)

    for task_id, task in non_terminal_tasks(state, runtime.current_wave):
        if task.stage == TaskStage.AWAITING_CONFIRMATION:
            runtime.accumulator.add_confirmation_task(task_id)
            progressed = True
            continue

        if task.stage == TaskStage.AWAITING_AUTH:
            runtime.accumulator.add_auth_task(task_id)
            progressed = True
            continue

        if _should_defer_during_input_interrupt(
            state=state,
            task_id=task_id,
            task=task,
            active_input_task_types=active_input_task_types,
        ):
            logger.info(
                "task_deferred_during_input_interrupt",
                task_id=task_id,
                active_task_ids=interrupt.task_ids,
            )
            continue

        dependency_progress = _apply_dependency_status(task=task, task_id=task_id, state=state)
        if dependency_progress is True:
            progressed = True
            continue
        if dependency_progress is None:
            continue

        executor = runtime.task_executors.get(task.type)
        if not executor:
            continue

        action = str(task.payload.get("action") or "").strip().lower()
        if not action:
            action = DEFAULT_OPERATION_BY_EXECUTOR[task.type]
            task.payload["action"] = action
        try:
            operation = operation_spec(task.type, action)
        except ValueError as exc:
            fail_task(task, str(exc))
            logger.warning(
                "worker_operation_rejected",
                executor=task.type,
                canonical_action=action,
                reason="unregistered",
            )
            progressed = True
            continue
        logger.info(
            "worker_operation_dispatch",
            domain=operation.domain,
            executor=operation.executor,
            canonical_action=operation.action,
            risk=operation.risk,
            requires_confirmation=operation.requires_confirmation,
            requires_pin=operation.requires_pin,
        )

        if _apply_mandate_gate_failure(state=state, task=task, runtime=runtime):
            progressed = True
            continue

        await executor.execute(task, task_id, runtime.ctx)
        if task.type in TRANSACTION_TASK_TYPES:
            for propagated_task_id in _propagate_batch_source_selection(
                state=state,
                selected_task_id=task_id,
                locale=runtime.locale,
            ):
                propagated_task = get_task(state, propagated_task_id)
                if propagated_task and propagated_task.stage == TaskStage.AWAITING_CONFIRMATION:
                    runtime.accumulator.add_confirmation_task(propagated_task_id)
        progressed = True

    if not progressed:
        _cancel_deadlocked_wave_tasks(state=state, current_wave=runtime.current_wave)

    _promote_deferred_confirmations(state=state, runtime=runtime)


__all__ = ["execute_current_wave_tasks"]
