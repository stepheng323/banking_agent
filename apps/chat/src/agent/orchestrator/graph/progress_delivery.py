"""Delivery loop for user-visible orchestrator progress updates."""

from __future__ import annotations

import asyncio
from typing import Any

from apps.chat.src.agent.orchestrator.graph.progress import (
    MAX_PROGRESS_MESSAGES,
    PROGRESS_POLL_INTERVAL_SECONDS,
    TurnProgressTracker,
    is_progress_stage_user_visible,
    next_progress_delay_seconds,
    render_progress_message,
    seconds_until_progress_eligible,
    should_emit_progress,
)
from shared.messaging.outbox import enqueue_outbox_say, enqueue_outbox_typing
from shared.queue.adapter import QueuePublisher
from shared.services.delivery_models import DeliveryAttemptResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorProgressDelivery:
    """Send typing indicators and bounded visible progress messages for one turn."""

    def __init__(self, publisher: QueuePublisher) -> None:
        self.publisher = publisher

    async def deliver_update(
        self,
        *,
        phone_number: str,
        channel: str,
        text: str,
        metadata: dict[str, Any],
        thread_id: str,
        stage_key: str,
        progress_count: int,
        turn_id: str,
    ) -> DeliveryAttemptResult:
        try:
            result = await enqueue_outbox_say(
                self.publisher,
                phone_number,
                channel,
                text,
                metadata=metadata,
            )
            logger.info(
                "progress_delivery_attempt",
                thread_id=thread_id,
                turn_id=turn_id,
                stage_key=stage_key,
                progress_count_before_attempt=progress_count,
                dedupe_key=metadata.get("dedupe_key"),
                delivery_status=result.status,
            )
            return result
        except Exception as exc:
            logger.warning(
                "progress_message_send_failed",
                thread_id=thread_id,
                turn_id=turn_id,
                stage_key=stage_key,
                progress_count=progress_count,
                error=str(exc),
            )
            return DeliveryAttemptResult(status="failed", error=str(exc))

    async def run_updates(
        self,
        *,
        tracker: TurnProgressTracker,
        phone_number: str,
        channel: str,
        channel_identity: str | None = None,
        inbound_message_id: str | None,
        thread_id: str,
        turn_id: str,
        enable_initial_typing: bool,
    ) -> None:
        deduped_progress_keys: set[str] = set()
        delivery_target = channel_identity if channel != "whatsapp" and channel_identity else phone_number

        if enable_initial_typing:
            try:
                await enqueue_outbox_typing(
                    self.publisher,
                    delivery_target,
                    channel,
                    metadata={
                        "inbound_message_id": inbound_message_id,
                        "dedupe_key": f"{thread_id}:{turn_id}:typing:0",
                    },
                )
            except Exception as exc:
                logger.warning("initial_typing_indicator_failed", error=str(exc))

        try:
            while True:
                snapshot = await tracker.snapshot()
                if snapshot.progress_count >= MAX_PROGRESS_MESSAGES:
                    return

                if not snapshot.stage_key:
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue

                if not is_progress_stage_user_visible(snapshot.stage_key):
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue

                next_delay = next_progress_delay_seconds(snapshot.stage_key, snapshot.progress_count)
                if next_delay is None:
                    return

                wait_seconds = seconds_until_progress_eligible(snapshot)
                if wait_seconds is None:
                    return
                if not should_emit_progress(snapshot):
                    await tracker.wait_for_update(min(PROGRESS_POLL_INTERVAL_SECONDS, wait_seconds))
                    continue

                text = render_progress_message(
                    stage_key=snapshot.stage_key,
                    progress_count=snapshot.progress_count,
                    locale=snapshot.locale,
                    stage_metadata=snapshot.stage_metadata,
                )
                dedupe_key = f"{thread_id}:{turn_id}:progress:{snapshot.progress_count}:{snapshot.stage_key}"
                if dedupe_key in deduped_progress_keys:
                    await tracker.wait_for_update(PROGRESS_POLL_INTERVAL_SECONDS)
                    continue
                metadata = {
                    "dedupe_key": dedupe_key,
                    "progress_stage": snapshot.stage_key,
                    "progress_count": snapshot.progress_count,
                    "progress_turn_id": turn_id,
                    "inbound_message_id": inbound_message_id,
                    "force_typing_indicator": True,
                }
                delivery_task = asyncio.create_task(
                    self.deliver_update(
                        phone_number=delivery_target,
                        channel=channel,
                        text=text,
                        metadata=metadata,
                        thread_id=thread_id,
                        stage_key=snapshot.stage_key,
                        progress_count=snapshot.progress_count,
                        turn_id=turn_id,
                    )
                )
                try:
                    delivery_result = await asyncio.shield(delivery_task)
                except asyncio.CancelledError:
                    await delivery_task
                    raise
                if delivery_result.delivered:
                    await tracker.record_progress_sent()
                    continue
                if delivery_result.status in {"deduped_completed", "deduped_resumed"}:
                    deduped_progress_keys.add(dedupe_key)
        except asyncio.CancelledError:
            raise
