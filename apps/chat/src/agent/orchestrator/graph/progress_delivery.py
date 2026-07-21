"""Delivery loop for user-visible orchestrator progress updates."""

from __future__ import annotations

import asyncio
from typing import Any

from apps.chat.src.agent.orchestrator.graph.progress import (
    MAX_PROGRESS_MESSAGES,
    PROGRESS_POLL_INTERVAL_SECONDS,
    PROGRESS_SETTLE_WINDOW_SECONDS,
    TurnProgressSnapshot,
    TurnProgressTracker,
    is_progress_stage_user_visible,
    next_progress_delay_seconds,
    render_progress_message,
    seconds_until_progress_eligible,
    should_emit_progress,
)
from banking.messaging.delivery.models import DeliveryAttemptResult
from shared.messaging.outbox import enqueue_outbox_say
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _stage_identity(snapshot: TurnProgressSnapshot) -> tuple[str | None, float | None]:
    return snapshot.stage_key, snapshot.stage_started_at


class OrchestratorProgressDelivery:
    """Send bounded visible progress messages for one turn."""

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
        del enable_initial_typing
        deduped_progress_keys: set[str] = set()
        delivery_target = channel_identity if channel != "whatsapp" and channel_identity else phone_number

        try:
            while True:
                snapshot = await tracker.snapshot()
                if snapshot.completed:
                    return
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

                candidate_identity = _stage_identity(snapshot)
                settle_deadline = asyncio.get_running_loop().time() + PROGRESS_SETTLE_WINDOW_SECONDS
                while True:
                    remaining = settle_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await tracker.wait_for_update(remaining)
                    settled_snapshot = await tracker.snapshot()
                    if settled_snapshot.completed:
                        logger.info(
                            "suppressed_final_ready",
                            stage_key=snapshot.stage_key,
                            progress_count=snapshot.progress_count,
                        )
                        return
                    if _stage_identity(settled_snapshot) != candidate_identity:
                        logger.info(
                            "stage_replaced",
                            prior_stage_key=snapshot.stage_key,
                            current_stage_key=settled_snapshot.stage_key,
                            progress_count=snapshot.progress_count,
                        )
                        break
                settled_snapshot = await tracker.snapshot()
                if settled_snapshot.completed:
                    logger.info(
                        "suppressed_final_ready",
                        stage_key=snapshot.stage_key,
                        progress_count=snapshot.progress_count,
                    )
                    return
                if _stage_identity(settled_snapshot) != candidate_identity:
                    continue
                if not should_emit_progress(settled_snapshot):
                    continue
                snapshot = settled_snapshot
                assert snapshot.stage_key is not None
                logger.info(
                    "progress_settled",
                    stage_key=snapshot.stage_key,
                    progress_count=snapshot.progress_count,
                    settle_window_ms=round(PROGRESS_SETTLE_WINDOW_SECONDS * 1000),
                )

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
                    # Completion must not wait for a progress outbox call that
                    # has already started. Cancellation cannot retract a call
                    # the transport has already accepted, but it keeps the
                    # final response path independent of that rare race.
                    delivery_task.cancel()
                    raise
                if delivery_result.delivered:
                    await tracker.record_progress_sent()
                    continue
                if delivery_result.status in {"deduped_completed", "deduped_resumed"}:
                    deduped_progress_keys.add(dedupe_key)
                    await tracker.record_progress_sent()
                    logger.info(
                        "progress_delivery_deduped_consumed",
                        stage_key=snapshot.stage_key,
                        progress_count=snapshot.progress_count,
                    )
                    continue
                logger.warning(
                    "delivery_failed",
                    stage_key=snapshot.stage_key,
                    progress_count=snapshot.progress_count,
                    delivery_status=delivery_result.status,
                )
                return
        except asyncio.CancelledError:
            raise
