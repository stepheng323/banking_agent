from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.auth_gate_updates import _build_auth_gate_updates
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import choose_wave_blocker
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_updates import (
    _build_confirmation_gate_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts import (
    _build_missing_field_interrupt_updates,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import (
    all_existing_tasks_terminal,
    task_log_shapes,
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
    return all_existing_tasks_terminal(state, current_wave)


def _advance_or_fail_stalled_wave(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    accumulator: ExecutionAccumulator,
) -> None:
    if _current_wave_is_terminal(state=state, current_wave=current_wave):
        if not accumulator.has_current_wave_index():
            accumulator.set_current_wave_index(state.current_wave_index + 1)
        return

    if accumulator.has_pending_interrupt():
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
        task_shapes=task_log_shapes(state, stalled),
    )
    accumulator.set_current_wave_index(state.current_wave_index + 1)


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

    if state.policy_notice:
        runtime.accumulator.set_outbox(_with_policy_notice(state, runtime.accumulator.get_outbox()))
        runtime.accumulator.clear_policy_notice()

    if blocker.kind == "confirmation":
        return _build_confirmation_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            task_ids=blocker.task_ids,
        )

    if blocker.kind == "auth":
        return _build_auth_gate_updates(
            state=state,
            current_wave=runtime.current_wave,
            agg=runtime.accumulator,
            locale=runtime.locale,
            task_ids=blocker.task_ids,
        )

    _advance_or_fail_stalled_wave(
        state=state,
        current_wave=runtime.current_wave,
        accumulator=runtime.accumulator,
    )
    return cast(dict[str, Any], runtime.accumulator.to_updates())


__all__ = ["finalize_execution_wave_updates"]
