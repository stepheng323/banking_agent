"""Background persistence helpers for delivered actionables."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from shared.messaging.intents import UiIntent
from shared.services import delivery_actionables
from shared.utils.logging import get_logger

logger = get_logger(__name__)

LogLatencySpan = Callable[..., None]


def schedule_actionable_persist(
    *,
    background_tasks: set[asyncio.Task[None]],
    phone_number: str,
    channel: str,
    intents: list[UiIntent],
    message_ids: list[str],
    log_latency_span: LogLatencySpan,
) -> None:
    actionable_payload = next((intent.actionable_payload for intent in intents if intent.actionable_payload), None)
    if not actionable_payload or not message_ids:
        return

    async def _run() -> None:
        background_start = time.perf_counter()
        try:
            await delivery_actionables.persist_actionable_if_any(
                phone_number=phone_number,
                channel=channel,
                intents=intents,
                message_ids=message_ids,
                strict_actionable=False,
            )
            log_latency_span(
                span="delivery_actionable_persist_background",
                duration_ms=(time.perf_counter() - background_start) * 1000,
                phone_number=phone_number,
                channel=channel,
                intent_count=len(intents),
            )
        except Exception as exc:
            logger.error(
                "delivery_actionable_background_failed",
                channel=channel,
                phone_number=phone_number,
                message_ids=message_ids,
                error=str(exc),
                exc_info=True,
            )

    task = asyncio.create_task(_run())
    background_tasks.add(task)

    def _cleanup(completed: asyncio.Task[None]) -> None:
        background_tasks.discard(completed)

    task.add_done_callback(_cleanup)


__all__ = ["schedule_actionable_persist"]
