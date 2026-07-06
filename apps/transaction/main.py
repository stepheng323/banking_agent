"""Standalone transaction worker service for VPS deployment."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from fastapi import FastAPI

from apps.transaction.dependencies import TransactionWorkerConsumers, setup_transaction_worker_consumers
from shared.cache.distributed_lock import RedisDistributedLock, RedisLockTimeoutError
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.observability.loop_health import LoopHealthRegistry
from shared.observability.readiness import dependency_readiness
from shared.queue.contracts import TopicType, get_contract_by_topic
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.runtime_ownership import build_runtime_status
from shared.utils.logging import configure_logger, get_logger

configure_logger()
logger = get_logger(__name__)
_loop_health = LoopHealthRegistry()

TRANSACTION_TOPICS: tuple[TopicType, ...] = (
    "transaction.execute",
    "direct_transfer.reconcile",
    "transaction_debit.process",
    "transaction_debit.reconcile",
    "transaction_debit.refund",
    "transaction_debit.refund_reconcile",
    "funding.process",
    "funding.reconcile",
    "bill.fulfill",
    "bill.reconcile",
    "payout.process",
    "payout.reconcile",
    "refund.process",
    "refund.reconcile",
    "ledger.reconcile.postings",
    "ledger.reconcile.exposure",
)

_worker_task: asyncio.Task[None] | None = None
_direct_transfer_reconciliation_task: asyncio.Task[None] | None = None
_transaction_debit_reconciliation_task: asyncio.Task[None] | None = None
_transaction_debit_refund_reconciliation_task: asyncio.Task[None] | None = None
_funding_reconciliation_task: asyncio.Task[None] | None = None
_bill_reconciliation_task: asyncio.Task[None] | None = None
_payout_reconciliation_task: asyncio.Task[None] | None = None
_refund_reconciliation_task: asyncio.Task[None] | None = None
_ledger_reconciliation_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None
_STALE_CLAIM_INTERVAL_SECONDS = 30.0


class _ReconciliationConsumer(Protocol):
    async def process_job(self, payload: dict[str, Any]) -> Any: ...


def _enabled_domain_flags() -> dict[str, bool]:
    return dict.fromkeys(
        (
            "transaction",
            "direct_transfer_reconcile",
            "transaction_debit",
            "transaction_debit_reconcile",
            "transaction_debit_refund",
            "transaction_debit_refund_reconcile",
            "funding",
            "funding_reconcile",
            "bill_fulfill",
            "bill_reconcile",
            "payout",
            "payout_reconcile",
            "refund",
            "refund_reconcile",
            "ledger_posting_reconcile",
            "ledger_exposure_reconcile",
        ),
        True,
    )


def _mark_loop_disabled(loop_name: str) -> None:
    _loop_health.get(loop_name).mark_disabled()


def _mark_loop_started(loop_name: str) -> None:
    _loop_health.get(loop_name).mark_started()


def _mark_loop_success(loop_name: str, *, domain: str) -> None:
    _loop_health.get(loop_name).mark_success()
    logger.debug(f"{loop_name}_tick_completed", domain=domain)


def _mark_loop_lock_skipped(loop_name: str, *, domain: str) -> None:
    logger.debug(f"{loop_name}_tick_skipped_lock_held", domain=domain)


def _mark_loop_failure(loop_name: str, *, domain: str, exc: Exception) -> None:
    _loop_health.get(loop_name).mark_failure(exc)
    emit_operational_event(
        f"{loop_name}_tick_failed",
        severity="high",
        domain=domain,
        details={"error_type": type(exc).__name__},
        logger=logger,
    )


async def _run_generic_reconciliation_loop(
    stop_event: asyncio.Event,
    loop_name: str,
    domain: str,
    interval_seconds: int,
    consumer: _ReconciliationConsumer,
) -> None:
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info(f"{loop_name}_loop_disabled")
        return

    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.debug(f"{loop_name}_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:{loop_name}:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain=domain)
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain=domain, exc=exc)
            logger.error(f"{loop_name}_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await consumer.process_job({})
            _mark_loop_success(loop_name, domain=domain)
        except Exception as exc:
            _mark_loop_failure(loop_name, domain=domain, exc=exc)
            logger.error(f"{loop_name}_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning(f"{loop_name}_lock_release_failed", error=str(exc))


async def _run_direct_transfer_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="direct_transfer_reconciliation",
        domain="direct_transfer",
        interval_seconds=int(settings.direct_transfer_reconciliation_interval_seconds or 0),
        consumer=consumers.direct_transfer_reconciliation,
    )


def _enabled_stream_names() -> list[str]:
    stream_names: list[str] = []
    for topic in TRANSACTION_TOPICS:
        stream_name = get_contract_by_topic(topic).redis_stream_name
        if stream_name:
            stream_names.append(stream_name)
    return stream_names


async def _process_stream_record(
    consumers: TransactionWorkerConsumers,
    stream_consumer: RedisStreamConsumer,
    record: RedisStreamRecord,
) -> None:
    try:
        if record.topic == "transaction.execute":
            await consumers.transaction.process_transaction(record.payload)
        elif record.topic == "direct_transfer.reconcile":
            await consumers.direct_transfer_reconciliation.process_job(record.payload)
        elif record.topic == "transaction_debit.process":
            await consumers.transaction_debit.process_job(record.payload)
        elif record.topic == "transaction_debit.reconcile":
            await consumers.transaction_debit_reconciliation.process_job(record.payload)
        elif record.topic == "transaction_debit.refund":
            await consumers.transaction_debit_refund.process_job(record.payload)
        elif record.topic == "transaction_debit.refund_reconcile":
            await consumers.transaction_debit_refund_reconciliation.process_job(record.payload)
        elif record.topic == "funding.process":
            await consumers.funding.process_job(record.payload)
        elif record.topic == "funding.reconcile":
            await consumers.funding_reconciliation.process_job(record.payload)
        elif record.topic == "bill.fulfill":
            await consumers.bill_fulfillment.process_job(record.payload)
        elif record.topic == "bill.reconcile":
            await consumers.bill_reconciliation.process_job(record.payload)
        elif record.topic == "payout.process":
            await consumers.payout.process_job(record.payload)
        elif record.topic == "payout.reconcile":
            await consumers.payout_reconciliation.process_job(record.payload)
        elif record.topic == "refund.process":
            await consumers.refund.process_job(record.payload)
        elif record.topic == "refund.reconcile":
            await consumers.refund_reconciliation.process_job(record.payload)
        elif record.topic == "ledger.reconcile.postings":
            await consumers.ledger_posting_reconciliation.process_job(record.payload)
        elif record.topic == "ledger.reconcile.exposure":
            await consumers.ledger_exposure_reconciliation.process_job(record.payload)
        else:
            logger.warning("transaction_worker_unknown_stream_topic", topic=record.topic, stream=record.stream_name)
        await stream_consumer.ack(record.stream_name, record.record_id)
    except Exception as exc:
        emit_operational_event(
            "transaction_worker_stream_record_failed",
            severity="high",
            domain="queue",
            identifiers={"topic": record.topic, "stream": record.stream_name, "record_id": record.record_id},
            details={"error_type": type(exc).__name__},
            logger=logger,
        )
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
    logger.debug("transaction_stream_worker_started")
    await stream_consumer.ensure_groups()
    last_stale_claim = -_STALE_CLAIM_INTERVAL_SECONDS

    while not stop_event.is_set():
        now = time.monotonic()
        if now - last_stale_claim >= _STALE_CLAIM_INTERVAL_SECONDS:
            claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
            for record in claimed:
                await _process_stream_record(consumers, stream_consumer, record)
            last_stale_claim = now

        records = await stream_consumer.consume(count=25, block_ms=settings.transaction_worker_stream_block_ms)
        for record in records:
            await _process_stream_record(consumers, stream_consumer, record)


async def _run_transaction_debit_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="transaction_debit_reconciliation",
        domain="bill",
        interval_seconds=int(settings.transaction_debit_reconciliation_interval_seconds or 0),
        consumer=consumers.transaction_debit_reconciliation,
    )


async def _run_bill_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="bill_reconciliation",
        domain="bill",
        interval_seconds=int(settings.bill_reconciliation_interval_seconds or 0),
        consumer=consumers.bill_reconciliation,
    )


async def _run_transaction_debit_refund_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="transaction_debit_refund_reconciliation",
        domain="refund",
        interval_seconds=int(settings.refund_reconciliation_interval_seconds or 0),
        consumer=consumers.transaction_debit_refund_reconciliation,
    )


async def _run_payout_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="payout_reconciliation",
        domain="payout",
        interval_seconds=int(settings.payout_reconciliation_interval_seconds or 0),
        consumer=consumers.payout_reconciliation,
    )


async def _run_funding_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="funding_reconciliation",
        domain="funding",
        interval_seconds=int(settings.funding_reconciliation_interval_seconds or 0),
        consumer=consumers.funding_reconciliation,
    )


async def _run_refund_reconciliation_loop(stop_event: asyncio.Event) -> None:
    consumers = setup_transaction_worker_consumers()
    await _run_generic_reconciliation_loop(
        stop_event,
        loop_name="refund_reconciliation",
        domain="refund",
        interval_seconds=int(settings.refund_reconciliation_interval_seconds or 0),
        consumer=consumers.refund_reconciliation,
    )


def _mark_loop_dependency_skipped(loop_name: str, *, domain: str, dependency: str) -> None:
    emit_operational_event(
        f"{loop_name}_tick_skipped_dependency_not_completed",
        severity="warning",
        domain=domain,
        details={"dependency": dependency},
        logger=logger,
    )


async def _run_locked_reconciliation_consumer(
    loop_name: str,
    consumer: _ReconciliationConsumer,
    *,
    lock_ttl_seconds: int,
) -> bool:
    lock = RedisDistributedLock(
        RedisClient.get_client(),
        key=f"{settings.project_name}:{loop_name}:{settings.runtime.infrastructure_environment}",
        ttl_seconds=lock_ttl_seconds,
    )
    try:
        await lock.acquire(wait_seconds=0.1)
    except RedisLockTimeoutError:
        _mark_loop_lock_skipped(loop_name, domain="ledger")
        return False
    except Exception as exc:
        _mark_loop_failure(loop_name, domain="ledger", exc=exc)
        logger.error(f"{loop_name}_lock_failed", error=str(exc), exc_info=True)
        return False

    try:
        await consumer.process_job({})
        _mark_loop_success(loop_name, domain="ledger")
        return True
    except Exception as exc:
        _mark_loop_failure(loop_name, domain="ledger", exc=exc)
        logger.error(f"{loop_name}_tick_failed", error=str(exc), exc_info=True)
        return False
    finally:
        try:
            await lock.release()
        except Exception as exc:
            logger.warning(f"{loop_name}_lock_release_failed", error=str(exc))


async def _run_ledger_reconciliation_tick(
    consumers: TransactionWorkerConsumers,
    *,
    lock_ttl_seconds: int,
) -> None:
    posting_completed = await _run_locked_reconciliation_consumer(
        "ledger_posting_reconciliation",
        consumers.ledger_posting_reconciliation,
        lock_ttl_seconds=lock_ttl_seconds,
    )
    if not posting_completed:
        _mark_loop_dependency_skipped(
            "ledger_exposure_reconciliation",
            domain="ledger",
            dependency="ledger_posting_reconciliation",
        )
        logger.debug("ledger_exposure_reconciliation_tick_skipped_posting_not_completed")
        return

    await _run_locked_reconciliation_consumer(
        "ledger_exposure_reconciliation",
        consumers.ledger_exposure_reconciliation,
        lock_ttl_seconds=lock_ttl_seconds,
    )


async def _run_ledger_reconciliation_loop(stop_event: asyncio.Event) -> None:
    interval_seconds = int(settings.ledger_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled("ledger_posting_reconciliation")
        _mark_loop_disabled("ledger_exposure_reconciliation")
        logger.debug("ledger_posting_reconciliation_loop_disabled")
        logger.debug("ledger_exposure_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started("ledger_posting_reconciliation")
    _mark_loop_started("ledger_exposure_reconciliation")
    logger.debug("ledger_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        await _run_ledger_reconciliation_tick(consumers, lock_ttl_seconds=lock_ttl_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown logic for the standalone transaction worker."""
    global _worker_task, _direct_transfer_reconciliation_task
    global _transaction_debit_reconciliation_task, _transaction_debit_refund_reconciliation_task
    global _funding_reconciliation_task, _bill_reconciliation_task, _payout_reconciliation_task
    global _refund_reconciliation_task, _ledger_reconciliation_task, _stop_event

    logger.info("transaction_worker_service_starting", **build_runtime_status("transaction-worker"))

    _stop_event = asyncio.Event()

    _worker_task = asyncio.create_task(
        _run_transaction_stream_worker(_stop_event),
        name="transaction-redis-worker",
    )
    _funding_reconciliation_task = asyncio.create_task(
        _run_funding_reconciliation_loop(_stop_event),
        name="funding-reconciliation-loop",
    )
    _direct_transfer_reconciliation_task = asyncio.create_task(
        _run_direct_transfer_reconciliation_loop(_stop_event),
        name="direct-transfer-reconciliation-loop",
    )
    _transaction_debit_reconciliation_task = asyncio.create_task(
        _run_transaction_debit_reconciliation_loop(_stop_event),
        name="transaction-debit-reconciliation-loop",
    )
    _bill_reconciliation_task = asyncio.create_task(
        _run_bill_reconciliation_loop(_stop_event),
        name="bill-reconciliation-loop",
    )
    _transaction_debit_refund_reconciliation_task = asyncio.create_task(
        _run_transaction_debit_refund_reconciliation_loop(_stop_event),
        name="transaction-debit-refund-reconciliation-loop",
    )
    _payout_reconciliation_task = asyncio.create_task(
        _run_payout_reconciliation_loop(_stop_event),
        name="payout-reconciliation-loop",
    )
    _refund_reconciliation_task = asyncio.create_task(
        _run_refund_reconciliation_loop(_stop_event),
        name="refund-reconciliation-loop",
    )
    _ledger_reconciliation_task = asyncio.create_task(
        _run_ledger_reconciliation_loop(_stop_event),
        name="ledger-reconciliation-loop",
    )
    logger.info(
        "transaction_worker_active",
        **build_runtime_status("transaction-worker"),
    )

    yield

    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
        _worker_task = None
    for task in (
        _direct_transfer_reconciliation_task,
        _transaction_debit_reconciliation_task,
        _bill_reconciliation_task,
        _transaction_debit_refund_reconciliation_task,
        _funding_reconciliation_task,
        _payout_reconciliation_task,
        _refund_reconciliation_task,
        _ledger_reconciliation_task,
    ):
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    _direct_transfer_reconciliation_task = None
    _transaction_debit_reconciliation_task = None
    _bill_reconciliation_task = None
    _transaction_debit_refund_reconciliation_task = None
    _funding_reconciliation_task = None
    _payout_reconciliation_task = None
    _refund_reconciliation_task = None
    _ledger_reconciliation_task = None
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
    readiness_result = await dependency_readiness(require_db=True, require_redis=True)
    return {
        "status": readiness_result["status"],
        "service": "transaction-worker",
        "worker_enabled": True,
        "enabled_domains": _enabled_domain_flags(),
        "async_transport": "redis",
        "checks": readiness_result["checks"],
        "loop_health": _loop_health.snapshot(),
        "funding_reconciliation_interval_seconds": settings.funding_reconciliation_interval_seconds,
        "direct_transfer_reconciliation_interval_seconds": settings.direct_transfer_reconciliation_interval_seconds,
        "transaction_debit_reconciliation_interval_seconds": settings.transaction_debit_reconciliation_interval_seconds,
        "bill_reconciliation_interval_seconds": settings.bill_reconciliation_interval_seconds,
        "payout_reconciliation_interval_seconds": settings.payout_reconciliation_interval_seconds,
        "refund_reconciliation_interval_seconds": settings.refund_reconciliation_interval_seconds,
        "ledger_reconciliation_interval_seconds": settings.ledger_reconciliation_interval_seconds,
        "runtime": build_runtime_status("transaction-worker"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
