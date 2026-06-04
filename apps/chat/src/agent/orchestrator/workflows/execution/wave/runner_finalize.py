from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.auth_gate_updates import _build_auth_gate_updates
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import choose_wave_blocker
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES, _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_updates import (
    _build_confirmation_gate_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts import (
    _build_missing_field_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import ExecutionWaveRuntime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import _fail_stalled_wave_tasks
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _current_wave_is_terminal(
    *,
    state: OrchestratorState,
    current_wave: list[str],
) -> bool:
    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue
        if task.stage not in TERMINAL_STAGES:
            return False
    return True


def _advance_or_fail_stalled_wave(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    updates: dict[str, Any],
) -> None:
    if _current_wave_is_terminal(state=state, current_wave=current_wave):
        if "current_wave_index" not in updates:
            updates["current_wave_index"] = state.current_wave_index + 1
        return

    if "pending_interrupt" in updates:
        return

    stalled = _fail_stalled_wave_tasks(
        state=state,
        current_wave=current_wave,
        reason="task made no terminal or blocking progress",
    )
    logger.error(
        "advance_wave_stalled_without_stop_condition",
        wave=current_wave,
        stalled_tasks=stalled,
        task_shapes=[
            {
                "task_id": task_id,
                "type": state.tasks[task_id].type,
                "stage": cast(TaskStage, state.tasks[task_id].stage).value,
                "action": state.tasks[task_id].payload.get("action"),
            }
            for task_id in stalled
            if task_id in state.tasks
        ],
    )
    updates["current_wave_index"] = state.current_wave_index + 1


def finalize_execution_wave_updates(
    *,
    state: OrchestratorState,
    runtime: ExecutionWaveRuntime,
) -> dict[str, Any]:
    blocker = choose_wave_blocker(state=state, current_wave=runtime.current_wave, agg=runtime.accumulator)
    if blocker.kind == "input":
        return _build_missing_field_interrupt_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
        )

    updates = runtime.accumulator.updates
    if state.policy_notice:
        existing = updates.get("outbox", [])
        updates["outbox"] = _with_policy_notice(state, existing)
        updates["policy_notice"] = None

    if blocker.kind == "confirmation":
        return _build_confirmation_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            updates=updates,
            task_ids=blocker.task_ids,
        )

    if blocker.kind == "auth":
        return _build_auth_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            updates=updates,
            task_ids=blocker.task_ids,
        )

    _advance_or_fail_stalled_wave(
        state=state,
        current_wave=runtime.current_wave,
        updates=updates,
    )
    return cast(dict[str, Any], updates)


__all__ = ["finalize_execution_wave_updates"]
