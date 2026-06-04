from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TRANSACTION_TASK_TYPES
from apps.chat.src.agent.orchestrator.workflows.execution.source_selection import (
    _propagate_batch_source_selection,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import get_task, non_terminal_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_task_guards import (
    _active_input_task_types,
    _apply_dependency_status,
    _apply_mandate_gate_failure,
    _cancel_deadlocked_wave_tasks,
    _should_defer_during_input_interrupt,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def execute_current_wave_tasks(
    *,
    state: OrchestratorState,
    runtime: ExecutionWaveRuntime,
) -> None:
    progressed = False
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
                active_task_ids=state.last_interrupt.task_ids if state.last_interrupt is not None else [],
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


__all__ = ["execute_current_wave_tasks"]
