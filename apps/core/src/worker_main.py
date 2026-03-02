"""Pure worker entrypoint for chat-critical ECS consumers."""

import asyncio
import signal

from apps.core.src.runtime.core_chat_dependencies import setup_core_consumers
from apps.core.src.runtime_bootstrap import warm_runtime
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


async def _process_stream_record(
    consumer,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> None:
    try:
        await consumer.process_record(record.topic, record.payload)
        await stream_consumer.ack(record.stream_name, record.record_id)
    except Exception as exc:
        logger.error(
            "core_chat_stream_record_failed",
            stream=record.stream_name,
            record_id=record.record_id,
            topic=record.topic,
            error=str(exc),
            exc_info=True,
        )


async def _run_stream_loop(consumer, stream_consumer: RedisStreamConsumer) -> None:
    logger.info("core_chat_stream_loop_starting", streams=stream_consumer.stream_names)
    await stream_consumer.ensure_groups()

    while True:
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        for record in claimed:
            await _process_stream_record(consumer, stream_consumer, record)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        for record in records:
            await _process_stream_record(consumer, stream_consumer, record)


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run chat-critical consumer in a long-lived worker process."""
    logger.info("starting_core_chat_worker")
    await warm_runtime()

    message_consumer, stream_consumer = setup_core_consumers()
    stream_task = asyncio.create_task(
        _run_stream_loop(message_consumer, stream_consumer),
        name="core-chat-stream-loop",
    )

    worker_stop_event = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()

        def _request_shutdown(sig: signal.Signals) -> None:
            logger.info("core_chat_worker_shutdown_signal", signal=sig.name)
            worker_stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown, sig)
            except NotImplementedError:
                pass

    try:
        await worker_stop_event.wait()
    finally:
        logger.info("stopping_core_chat_worker")
        stream_task.cancel()
        await asyncio.gather(stream_task, return_exceptions=True)
        logger.info("core_chat_worker_stopped")


def main() -> None:
    """Synchronous process entrypoint."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
