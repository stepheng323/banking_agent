"""Pure worker entrypoint for chat-critical ECS consumers."""

import asyncio
import signal
from datetime import UTC, datetime

from apps.chat.src.runtime.chat_worker_dependencies import setup_chat_consumers
from apps.chat.src.runtime_bootstrap import warm_runtime
from shared.config.settings import settings
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)
setup_core_consumers = setup_chat_consumers
_RUNTIME_WARMUP_TIMEOUT_SECONDS = 8.0


async def _process_stream_record(
    consumer,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> None:
    try:
        if _should_drop_stale(record):
            await stream_consumer.ack(record.stream_name, record.record_id)
            return
        await consumer.process_record(record.topic, record.payload)
        await stream_consumer.ack(record.stream_name, record.record_id)
    except Exception as exc:
        logger.error(
            "chat_worker_stream_record_failed",
            stream=record.stream_name,
            record_id=record.record_id,
            topic=record.topic,
            error=str(exc),
            exc_info=True,
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


async def _run_stream_loop(consumer, stream_consumer: RedisStreamConsumer) -> None:
    logger.info("chat_worker_stream_loop_starting", streams=stream_consumer.stream_names)
    await stream_consumer.ensure_groups()

    while True:
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        for record in claimed:
            await _process_stream_record(consumer, stream_consumer, record)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        for record in records:
            await _process_stream_record(consumer, stream_consumer, record)


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

    message_consumer, stream_consumer = setup_core_consumers()
    stream_task = asyncio.create_task(
        _run_stream_loop(message_consumer, stream_consumer),
        name="chat-worker-stream-loop",
    )

    try:
        await worker_stop_event.wait()
    finally:
        logger.info("stopping_chat_worker")
        stream_task.cancel()
        await asyncio.gather(stream_task, return_exceptions=True)
        logger.info("chat_worker_stopped")


def main() -> None:
    """Synchronous process entrypoint."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
