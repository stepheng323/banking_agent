"""Progress delivery lifecycle helpers for graph invocations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressSnapshot, TurnProgressTracker
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery


@dataclass(frozen=True, slots=True)
class ProgressDeliveryRun:
    tracker: TurnProgressTracker
    task: asyncio.Task[None]


def start_progress_delivery(
    *,
    progress_delivery: OrchestratorProgressDelivery,
    tracker: TurnProgressTracker,
    phone_number: str,
    channel: str,
    channel_identity: str | None,
    inbound_message_id: str | None,
    thread_id: str,
    turn_id: str,
    enable_initial_typing: bool,
) -> ProgressDeliveryRun:
    task = asyncio.create_task(
        progress_delivery.run_updates(
            tracker=tracker,
            phone_number=phone_number,
            channel=channel,
            channel_identity=channel_identity,
            inbound_message_id=inbound_message_id,
            thread_id=thread_id,
            turn_id=turn_id,
            enable_initial_typing=enable_initial_typing,
        ),
        name="orchestrator_progress_updates",
    )
    return ProgressDeliveryRun(tracker=tracker, task=task)


async def stop_progress_delivery(run: ProgressDeliveryRun) -> TurnProgressSnapshot:
    mark_complete = getattr(run.tracker, "mark_turn_complete", None)
    if callable(mark_complete):
        await mark_complete()
    run.task.cancel()
    try:
        await run.task
    except asyncio.CancelledError:
        pass
    return await run.tracker.snapshot()


__all__ = ["ProgressDeliveryRun", "start_progress_delivery", "stop_progress_delivery"]
