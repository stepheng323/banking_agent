from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.control_state import execution_control_state
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_coordination import (
    _maybe_coordinate_batch_funding,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import finalize_execution_wave_updates
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import build_execution_wave_runtime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_tasks import (
    execute_current_wave_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import wave_position
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def advance_wave(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Execution Node.

    Iterates through tasks in current wave.
    Invokes Domain Workers.
    Aggregates outcomes and sets PendingInterrupt if blocked.
    """
    position = wave_position(state)
    if not position.has_current_wave:
        logger.info("advance_wave_skip", index=position.index, count=position.wave_count)
        return {}

    current_wave = position.current_wave
    surface = context_surface(state)
    logger.info(
        "advance_wave",
        index=position.index,
        tasks=current_wave,
        context_frames_len=surface.frame_count,
    )

    # [SAFETY] If pending_interrupt is already set (e.g. valid restoration), do NOT execute tasks.
    # Return it to force graph to stop/route correctly.
    control = execution_control_state(state)
    if control.has_pending_interrupt:
        logger.info("advance_wave_blocked_by_interrupt", kind=control.pending_interrupt_kind)
        return control.pending_interrupt_update()

    runtime = build_execution_wave_runtime(state=state, config=config, current_wave=current_wave)

    batch_block = await _maybe_coordinate_batch_funding(
        state=state,
        current_wave=current_wave,
        services=runtime.services,
        agg=runtime.accumulator,
        locale=runtime.locale,
    )
    if batch_block:
        return batch_block

    await execute_current_wave_tasks(state=state, runtime=runtime)
    return finalize_execution_wave_updates(state=state, runtime=runtime)
