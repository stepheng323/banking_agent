"""Execution wave engine with typed phase results."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.control_state import execution_control_state
from apps.chat.src.agent.orchestrator.workflows.execution.funding.batch_funding_coordination import (
    _maybe_coordinate_batch_funding,
)
from apps.chat.src.agent.orchestrator.workflows.execution.recipient_review import maybe_request_recipient_review
from apps.chat.src.agent.orchestrator.workflows.execution.wave.result import ExecutionWaveResult
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_finalize import finalize_execution_wave_updates
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_setup import build_execution_wave_runtime
from apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_tasks import execute_current_wave_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import wave_position
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def run_execution_wave(state: OrchestratorState, config: RunnableConfig) -> ExecutionWaveResult:
    """Run one execution wave and expose the explicit phase reached."""
    position = wave_position(state)
    if not position.has_current_wave:
        logger.info("advance_wave_skip", index=position.index, count=position.wave_count)
        return ExecutionWaveResult(updates={}, phase="no_current_wave", current_wave=[])

    current_wave = position.current_wave
    surface = context_surface(state)
    logger.info(
        "advance_wave",
        index=position.index,
        tasks=current_wave,
        context_frames_len=surface.frame_count,
    )

    control = execution_control_state(state)
    if control.has_pending_interrupt:
        logger.info("advance_wave_blocked_by_interrupt", kind=control.pending_interrupt_kind)
        return ExecutionWaveResult(
            updates=control.pending_interrupt_update(),
            phase="pending_interrupt",
            current_wave=current_wave,
            pending_interrupt_kind=control.pending_interrupt_kind,
        )

    runtime = build_execution_wave_runtime(state=state, config=config, current_wave=current_wave)

    recipient_review_block = maybe_request_recipient_review(
        state=state,
        current_wave=current_wave,
        agg=runtime.accumulator,
    )
    if recipient_review_block:
        return ExecutionWaveResult(
            updates=recipient_review_block,
            phase="recipient_review_block",
            current_wave=current_wave,
        )

    batch_block = await _maybe_coordinate_batch_funding(
        state=state,
        current_wave=current_wave,
        services=runtime.services,
        agg=runtime.accumulator,
        locale=runtime.locale,
    )
    if batch_block:
        return ExecutionWaveResult(
            updates=batch_block,
            phase="batch_funding_block",
            current_wave=current_wave,
        )

    await execute_current_wave_tasks(state=state, runtime=runtime)
    recipient_review_block = maybe_request_recipient_review(
        state=state,
        current_wave=current_wave,
        agg=runtime.accumulator,
    )
    if recipient_review_block:
        return ExecutionWaveResult(
            updates=recipient_review_block,
            phase="recipient_review_block",
            current_wave=current_wave,
            worker_phase_entered=True,
        )

    batch_block = await _maybe_coordinate_batch_funding(
        state=state,
        current_wave=current_wave,
        services=runtime.services,
        agg=runtime.accumulator,
        locale=runtime.locale,
        allow_existing_funding_plans=True,
    )
    if batch_block:
        return ExecutionWaveResult(
            updates=batch_block,
            phase="batch_funding_block",
            current_wave=current_wave,
            worker_phase_entered=True,
        )

    updates: dict[str, Any] = await finalize_execution_wave_updates(state=state, runtime=runtime)
    return ExecutionWaveResult(
        updates=updates,
        phase="finalized",
        current_wave=current_wave,
        worker_phase_entered=True,
    )


__all__ = ["run_execution_wave"]
