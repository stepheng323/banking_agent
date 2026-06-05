"""Pure worker entrypoint for chat-critical ECS consumers."""

import asyncio
import signal
from collections import OrderedDict
from datetime import UTC, datetime

from apps.chat.src.runtime.chat_worker_dependencies import setup_chat_consumers
from apps.chat.src.runtime.scheduler_dispatcher_dependencies import setup_schedule_dispatcher
from apps.chat.src.runtime_bootstrap import warm_runtime
from shared.cache.distributed_lock import RedisDistributedLock, RedisLockTimeoutError
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger
from apps.chat.src.queue_consumers.message_consumer import MessageConsumer

configure_logger()
logger = get_logger(__name__)
_RUNTIME_WARMUP_TIMEOUT_SECONDS = 8.0
_SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY = "_suppress_intermediate_input_prompt"


async def _process_stream_record(
    consumer,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> bool:
    try:
        if _should_drop_stale(record):
            await stream_consumer.ack(record.stream_name, record.record_id)
            return True
        await consumer.process_record(record.topic, record.payload)
        await stream_consumer.ack(record.stream_name, record.record_id)
        return True
    except Exception as exc:
        logger.error(
            "chat_worker_stream_record_failed",
            stream=record.stream_name,
            record_id=record.record_id,
            topic=record.topic,
            error=str(exc),
            exc_info=True,
        )
        return False


async def _process_stream_record_bounded(
    consumer,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
    semaphore: asyncio.Semaphore,
) -> bool:
    async with semaphore:
        return await _process_stream_record(consumer, stream_consumer, record)


def _record_ordering_key(record: RedisStreamRecord) -> str:
    payload = record.payload
    if record.topic == "message.received":
        channel = str(payload.get("channel") or "whatsapp")
        channel_user_id = str(payload.get("channel_user_id") or payload.get("phone_number") or "").strip()
        if channel_user_id:
            return f"message:{channel}:{channel_user_id}"
    if record.topic == "flow_event.process":
        channel = str(payload.get("channel") or "whatsapp")
        phone_number = str(payload.get("phone_number") or payload.get("channel_user_id") or "").strip()
        if phone_number:
            return f"flow:{channel}:{phone_number}"
    return f"{record.stream_name}:{record.record_id}"


def _group_stream_records(records: list[RedisStreamRecord]) -> list[list[RedisStreamRecord]]:
    groups: OrderedDict[str, list[RedisStreamRecord]] = OrderedDict()
    for record in records:
        groups.setdefault(_record_ordering_key(record), []).append(record)
    return list(groups.values())


def _mark_intermediate_message_record(record: RedisStreamRecord) -> None:
    if record.topic != "message.received":
        return
    metadata = record.payload.get("channel_metadata")
    record.payload["channel_metadata"] = {
        **(metadata if isinstance(metadata, dict) else {}),
        _SUPPRESS_INTERMEDIATE_INPUT_PROMPT_METADATA_KEY: True,
    }


async def _process_stream_record_group(
    consumer,
    stream_consumer: RedisStreamConsumer,
    records: list[RedisStreamRecord],
    semaphore: asyncio.Semaphore,
) -> None:
    for index, record in enumerate(records):
        if index < len(records) - 1:
            _mark_intermediate_message_record(record)
        processed = await _process_stream_record_bounded(consumer, stream_consumer, record, semaphore)
        if not processed:
            logger.warning(
                "chat_worker_ordered_group_halted",
                stream=record.stream_name,
                record_id=record.record_id,
                topic=record.topic,
                remaining_records=max(len(records) - index - 1, 0),
            )
            return


async def _process_stream_records(
    consumer,
    stream_consumer: RedisStreamConsumer,
    records: list[RedisStreamRecord],
    semaphore: asyncio.Semaphore,
) -> None:
    if not records:
        return
    await asyncio.gather(
        *(
            _process_stream_record_group(
                consumer,
                stream_consumer,
                group,
                semaphore,
            )
            for group in _group_stream_records(records)
        )
    )


def _should_drop_stale(record: RedisStreamRecord) -> bool:
    if record.topic != "message.received":
        return False

    raw_timestamp = record.payload.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return False

    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
    if age_seconds <= settings.chat_message_max_age_seconds:
        return False

    logger.warning(
        "chat_message_dropped_stale",
        topic=record.topic,
        record_id=record.record_id,
        age_seconds=round(age_seconds, 2),
        max_age_seconds=settings.chat_message_max_age_seconds,
    )
    return True


async def _run_stream_loop(consumer: MessageConsumer, stream_consumer: RedisStreamConsumer) -> None:
    max_concurrency = max(1, settings.chat_worker_max_concurrency)
    semaphore = asyncio.Semaphore(max_concurrency)
    logger.info(
        "chat_worker_stream_loop_starting",
        streams=stream_consumer.stream_names,
        max_concurrency=max_concurrency,
    )
    await stream_consumer.ensure_groups()

    while True:
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        await _process_stream_records(consumer, stream_consumer, claimed, semaphore)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        await _process_stream_records(consumer, stream_consumer, records, semaphore)


def _schedule_dispatcher_lock_key() -> str:
    return f"{settings.project_name}:schedule_dispatcher:{settings.runtime.infrastructure_environment}"


async def _dispatch_due_schedules_with_lock(lock_ttl_seconds: int) -> dict[str, int] | None:
    lock = RedisDistributedLock(
        RedisClient.get_client(),
        key=_schedule_dispatcher_lock_key(),
        ttl_seconds=lock_ttl_seconds,
    )
    acquired = False
    try:
        await lock.acquire(wait_seconds=0.1)
        acquired = True
        dispatcher = setup_schedule_dispatcher()
        return await dispatcher.dispatch_due()
    except RedisLockTimeoutError:
        logger.debug("schedule_dispatcher_tick_skipped_lock_held")
    except Exception as exc:
        logger.error("schedule_dispatcher_tick_failed", error=str(exc), exc_info=True)
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("schedule_dispatcher_lock_release_failed", error=str(exc))
    return None


async def _run_schedule_dispatcher_loop(stop_event: asyncio.Event) -> None:
    """Run scheduled transaction dispatch ticks inside the chat worker runtime."""
    interval_seconds = int(settings.schedule_dispatcher_interval_seconds or 0)
    if interval_seconds <= 0:
        logger.info("schedule_dispatcher_loop_disabled", reason="interval_non_positive")
        return

    lock_ttl_seconds = max(interval_seconds * 2, 60)
    logger.info(
        "schedule_dispatcher_loop_started",
        interval_seconds=interval_seconds,
        lock_ttl_seconds=lock_ttl_seconds,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        stats = await _dispatch_due_schedules_with_lock(lock_ttl_seconds)
        if stats is not None:
            logger.info("schedule_dispatcher_tick_completed", result=stats)


async def _warm_runtime_best_effort() -> None:
    """Warm optional dependencies without blocking queue consumption indefinitely."""
    try:
        await asyncio.wait_for(warm_runtime(), timeout=_RUNTIME_WARMUP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "chat_worker_runtime_warmup_timed_out",
            timeout_seconds=_RUNTIME_WARMUP_TIMEOUT_SECONDS,
            consequence="continuing_to_stream_consumer",
        )
    except Exception as exc:
        logger.warning(
            "chat_worker_runtime_warmup_failed",
            error=str(exc),
            consequence="continuing_to_stream_consumer",
            exc_info=True,
        )


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run chat-critical consumer in a long-lived worker process."""
    logger.info("starting_chat_worker", **build_runtime_status("chat-worker"))
    await _warm_runtime_best_effort()

    worker_stop_event = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()

        def _request_shutdown(sig: signal.Signals) -> None:
            logger.info("chat_worker_shutdown_signal", signal=sig.name)
            worker_stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig)
            except NotImplementedError:
                pass

    message_consumer, stream_consumer = setup_chat_consumers()
    stream_task = asyncio.create_task(
        _run_stream_loop(message_consumer, stream_consumer),
        name="chat-worker-stream-loop",
    )
    scheduler_task: asyncio.Task[None] | None = None
    if settings.enable_transfer_scheduling:
        scheduler_task = asyncio.create_task(
            _run_schedule_dispatcher_loop(worker_stop_event),
            name="chat-worker-schedule-dispatcher",
        )
    else:
        logger.info("schedule_dispatcher_loop_disabled", reason="transfer_scheduling_disabled")

    try:
        await worker_stop_event.wait()
    finally:
        logger.info("stopping_chat_worker")
        stream_task.cancel()
        tasks: list[asyncio.Task[None]] = [stream_task]
        if scheduler_task is not None:
            scheduler_task.cancel()
            tasks.append(scheduler_task)
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("chat_worker_stopped")


def main() -> None:
    """Synchronous process entrypoint."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
