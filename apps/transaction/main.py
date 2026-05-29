"""Standalone transaction worker service for VPS deployment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.transaction.dependencies import setup_transaction_worker_consumers
from apps.transaction.lambda_handler import _handler as transaction_lambda_handler
from shared.cache.distributed_lock import RedisDistributedLock, RedisLockTimeoutError
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.queue.contracts import TopicType, get_contract_by_topic
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.queue.sqs_poller import SQSPoller
from shared.runtime_ownership import build_runtime_status
from banking.transactions.runtime.consumers.funding_consumer import FundingConsumer
from banking.transactions.runtime.consumers.payout_consumer import PayoutConsumer
from banking.transactions.runtime.consumers.payout_reconciliation_consumer import PayoutReconciliationConsumer
from banking.transactions.runtime.consumers.refund_consumer import RefundConsumer
from banking.transactions.runtime.consumers.transaction_consumer import TransactionConsumer
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)

TRANSACTION_TOPICS: tuple[TopicType, ...] = (
    "transaction.execute",
    "funding.process",
    "payout.process",
    "payout.reconcile",
    "refund.process",
)

_worker_task: asyncio.Task[None] | None = None
_reconciliation_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


def _enabled_domain_flags() -> dict[str, bool]:
    return dict.fromkeys(("transaction", "funding", "payout", "payout_reconcile", "refund"), True)


async def _run_transaction_worker(stop_event: asyncio.Event) -> None:
    contract = get_contract_by_topic("transaction.execute")
    poller = SQSPoller(contract=contract, handler=transaction_lambda_handler.process_event)
    await poller.run(stop_event)


def _enabled_stream_names() -> list[str]:
    stream_names: list[str] = []
    for topic in TRANSACTION_TOPICS:
        stream_name = get_contract_by_topic(topic).redis_stream_name
        if stream_name:
            stream_names.append(stream_name)
    return stream_names


async def _process_stream_record(
    consumers: tuple[
        TransactionConsumer,
        FundingConsumer,
        PayoutConsumer,
        PayoutReconciliationConsumer,
        RefundConsumer,
    ],
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> None:
    transaction_consumer, funding_consumer, payout_consumer, payout_reconciliation_consumer, refund_consumer = consumers
    try:
        if record.topic == "transaction.execute":
            await transaction_consumer.process_transaction(record.payload)
        elif record.topic == "funding.process":
            await funding_consumer.process_job(record.payload)
        elif record.topic == "payout.process":
            await payout_consumer.process_job(record.payload)
        elif record.topic == "payout.reconcile":
            await payout_reconciliation_consumer.process_job(record.payload)
        elif record.topic == "refund.process":
            await refund_consumer.process_job(record.payload)
        else:
            logger.warning("transaction_worker_unknown_stream_topic", topic=record.topic, stream=record.stream_name)
        await stream_consumer.ack(record.stream_name, record.record_id)
    except Exception as exc:
        logger.error(
            "transaction_worker_stream_record_failed",
            topic=record.topic,
            stream=record.stream_name,
            record_id=record.record_id,
            error=str(exc),
            exc_info=True,
        )


async def _run_transaction_stream_worker(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    stream_consumer = RedisStreamConsumer(
        stream_names=_enabled_stream_names(),
        group_name=f"{settings.project_name}-transaction-worker-{settings.runtime.infrastructure_environment}",
    )
    logger.info("transaction_stream_worker_started", streams=stream_consumer.stream_names)
    await stream_consumer.ensure_groups()

    while not stop_event.is_set():
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        for record in claimed:
            await _process_stream_record(consumers, stream_consumer, record)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        for record in records:
            await _process_stream_record(consumers, stream_consumer, record)


async def _run_payout_reconciliation_loop(stop_event: asyncio.Event) -> None:
    interval_seconds = int(settings.payout_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        logger.info("payout_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    payout_reconciliation_consumer = consumers[3]
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    logger.info("payout_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:payout_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            logger.debug("payout_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            logger.error("payout_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await payout_reconciliation_consumer.process_job({})
            logger.info("payout_reconciliation_tick_completed")
        except Exception as exc:
            logger.error("payout_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("payout_reconciliation_lock_release_failed", error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown logic for the standalone transaction worker."""
    global _worker_task, _reconciliation_task, _stop_event

    logger.info("transaction_worker_service_starting", **build_runtime_status("transaction-worker"))

    _stop_event = asyncio.Event()
    domain_flags = _enabled_domain_flags()
    worker_enabled = settings.async_transport.lower() in {"aws", "redis"}
    if worker_enabled:
        if settings.async_transport.lower() == "redis":
            _worker_task = asyncio.create_task(
                _run_transaction_stream_worker(_stop_event),
                name="transaction-redis-worker",
            )
        else:
            _worker_task = asyncio.create_task(_run_transaction_worker(_stop_event), name="transaction-sqs-worker")
        _reconciliation_task = asyncio.create_task(
            _run_payout_reconciliation_loop(_stop_event),
            name="payout-reconciliation-loop",
        )
    else:
        logger.info(
            "transaction_worker_inactive",
            reason=f"async_transport={settings.async_transport}",
            topics=list(TRANSACTION_TOPICS),
            enabled_domains=domain_flags,
            **build_runtime_status("transaction-worker"),
        )

    yield

    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
        _worker_task = None
    if _reconciliation_task is not None:
        _reconciliation_task.cancel()
        await asyncio.gather(_reconciliation_task, return_exceptions=True)
        _reconciliation_task = None
    logger.info("transaction_worker_service_shutting_down")


app = FastAPI(title="Transaction Worker Service", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health_check() -> dict[str, object]:
    """Health endpoint."""
    return {
        "status": "healthy",
        "service": "transaction-worker",
        "ownership": build_runtime_status("transaction-worker"),
    }


@app.get("/ready")
async def readiness_check() -> dict[str, object]:
    """Readiness endpoint exposing worker and transport state."""
    return {
        "status": "ready",
        "service": "transaction-worker",
        "worker_enabled": settings.async_transport.lower() in {"aws", "redis"},
        "enabled_domains": _enabled_domain_flags(),
        "async_transport": settings.async_transport,
        "payout_reconciliation_interval_seconds": settings.payout_reconciliation_interval_seconds,
        "runtime": build_runtime_status("transaction-worker"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
