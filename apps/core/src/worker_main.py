"""Pure worker entrypoint for chat-critical ECS consumers."""

import asyncio
import signal

from apps.core.src.dependencies import setup_core_consumers
from apps.core.src.runtime_bootstrap import warm_runtime
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Run chat-critical consumers in a long-lived worker process."""
    logger.info("Starting Core Chat Worker...")
    await warm_runtime()

    message_consumer, flow_event_consumer = setup_core_consumers()
    consumers = [
        ("message_consumer", message_consumer),
        ("flow_event_consumer", flow_event_consumer),
    ]

    tasks = [asyncio.create_task(consumer.start(), name=name) for name, consumer in consumers]

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
                # Signal handlers are not available on some platforms (e.g., Windows)
                pass

    try:
        await worker_stop_event.wait()
    finally:
        logger.info("Stopping Core Chat Worker...")
        for _, consumer in consumers:
            consumer.stop()

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Core Chat Worker stopped")


def main() -> None:
    """Synchronous process entrypoint."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
