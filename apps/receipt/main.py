"""Standalone receipt worker service for VPS deployment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.receipt.dependencies import setup_receipt_worker_consumers
from apps.receipt.lambda_handler import _handler as receipt_lambda_handler
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.queue.contracts import get_contract_by_topic
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.queue.sqs_poller import SQSPoller
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)

_worker_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


async def _run_receipt_worker(stop_event: asyncio.Event) -> None:
    contract = get_contract_by_topic("receipt.process")
    poller = SQSPoller(contract=contract, handler=receipt_lambda_handler.process_event)
    await poller.run(stop_event)


async def _process_stream_record(
    consumer,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> None:
    try:
        await consumer.process_job(record.payload)
        await stream_consumer.ack(record.stream_name, record.record_id)
    except Exception as exc:
        logger.error(
            "receipt_worker_stream_record_failed",
            topic=record.topic,
            stream=record.stream_name,
            record_id=record.record_id,
            error=str(exc),
            exc_info=True,
        )


async def _run_receipt_stream_worker(stop_event: asyncio.Event) -> None:
    consumer = setup_receipt_worker_consumers()
    stream_name = get_contract_by_topic("receipt.process").redis_stream_name
    if not stream_name:
        raise RuntimeError("receipt_stream_not_configured")
    stream_consumer = RedisStreamConsumer(
        stream_names=[stream_name],
        group_name=f"{settings.project_name}-receipt-worker-{settings.runtime.infrastructure_environment}",
    )
    logger.info("receipt_stream_worker_started", streams=stream_consumer.stream_names)
    await stream_consumer.ensure_groups()

    while not stop_event.is_set():
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        for record in claimed:
            await _process_stream_record(consumer, stream_consumer, record)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        for record in records:
            await _process_stream_record(consumer, stream_consumer, record)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown logic for the receipt worker service."""
    global _worker_task, _stop_event

    logger.info("receipt_service_starting", **build_runtime_status("receipt-worker"))
    RedisClient.get_client()

    _stop_event = asyncio.Event()
    worker_enabled = settings.async_transport.lower() in {"aws", "redis"}
    if worker_enabled:
        if settings.async_transport.lower() == "redis":
            _worker_task = asyncio.create_task(_run_receipt_stream_worker(_stop_event), name="receipt-redis-worker")
        else:
            _worker_task = asyncio.create_task(_run_receipt_worker(_stop_event), name="receipt-sqs-worker")
    else:
        logger.info(
            "receipt_worker_inactive",
            reason=f"async_transport={settings.async_transport}",
            **build_runtime_status("receipt-worker"),
        )

    yield

    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
        _worker_task = None
    logger.info("receipt_service_shutting_down")


app = FastAPI(title="Receipt Service", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health_check() -> dict[str, object]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "receipt-worker",
        "ownership": build_runtime_status("receipt-worker"),
    }


@app.get("/ready")
async def readiness_check() -> dict[str, object]:
    """Readiness endpoint exposing worker and transport state."""
    return {
        "status": "ready",
        "service": "receipt-worker",
        "worker_enabled": settings.async_transport.lower() in {"aws", "redis"},
        "async_transport": settings.async_transport,
        "runtime": build_runtime_status("receipt-worker"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
