"""Standalone transaction worker service for VPS deployment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.transaction.dependencies import TransactionWorkerConsumers, setup_transaction_worker_consumers
from apps.transaction.lambda_handler import _handler as transaction_lambda_handler
from shared.cache.distributed_lock import RedisDistributedLock, RedisLockTimeoutError
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.observability.loop_health import LoopHealthRegistry
from shared.observability.readiness import dependency_readiness
from shared.queue.contracts import TopicType, get_contract_by_topic
from shared.queue.redis_stream_consumer import RedisStreamConsumer, RedisStreamRecord
from shared.queue.sqs_poller import SQSPoller
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
_ledger_posting_reconciliation_task: asyncio.Task[None] | None = None
_ledger_exposure_reconciliation_task: asyncio.Task[None] | None = None
_stop_event: asyncio.Event | None = None


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
    emit_operational_event(f"{loop_name}_tick_completed", severity="info", domain=domain)


def _mark_loop_lock_skipped(loop_name: str, *, domain: str) -> None:
    emit_operational_event(f"{loop_name}_tick_skipped_lock_held", severity="warning", domain=domain)


def _mark_loop_failure(loop_name: str, *, domain: str, exc: Exception) -> None:
    _loop_health.get(loop_name).mark_failure(exc)
    emit_operational_event(
        f"{loop_name}_tick_failed",
        severity="high",
        domain=domain,
        details={"error_type": type(exc).__name__},
    )


async def _run_transaction_worker(stop_event: asyncio.Event) -> None:
    contract = get_contract_by_topic("transaction.execute")
    poller = SQSPoller(contract=contract, handler=transaction_lambda_handler.process_event)
    await poller.run(stop_event)


async def _run_direct_transfer_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_health = _loop_health.get("direct_transfer_reconciliation")
    interval_seconds = int(settings.direct_transfer_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        loop_health.mark_disabled()
        logger.info("direct_transfer_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    consumer = consumers.direct_transfer_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    loop_health.mark_started()
    logger.info("direct_transfer_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:direct_transfer_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
            await consumer.process_job({})
            loop_health.mark_success()
            emit_operational_event(
                "direct_transfer_reconciliation_tick_completed",
                severity="info",
                domain="direct_transfer",
                details={"interval_seconds": interval_seconds},
            )
            logger.info("direct_transfer_reconciliation_tick_completed")
        except RedisLockTimeoutError:
            emit_operational_event(
                "direct_transfer_reconciliation_tick_skipped_lock_held",
                severity="warning",
                domain="direct_transfer",
            )
            logger.debug("direct_transfer_reconciliation_tick_skipped_lock_held")
        except Exception as exc:
            loop_health.mark_failure(exc)
            emit_operational_event(
                "direct_transfer_reconciliation_tick_failed",
                severity="high",
                domain="direct_transfer",
                details={"error_type": type(exc).__name__},
            )
            logger.error("direct_transfer_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("direct_transfer_reconciliation_lock_release_failed", error=str(exc))


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
    logger.info("transaction_stream_worker_started", streams=stream_consumer.stream_names)
    await stream_consumer.ensure_groups()

    while not stop_event.is_set():
        claimed = await stream_consumer.claim_stale(min_idle_ms=60_000, count=25)
        for record in claimed:
            await _process_stream_record(consumers, stream_consumer, record)

        records = await stream_consumer.consume(count=25, block_ms=5000)
        for record in records:
            await _process_stream_record(consumers, stream_consumer, record)


async def _run_transaction_debit_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "transaction_debit_reconciliation"
    interval_seconds = int(settings.transaction_debit_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("transaction_debit_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    consumer = consumers.transaction_debit_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("transaction_debit_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:transaction_debit_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
            await consumer.process_job({})
            _mark_loop_success(loop_name, domain="bill")
            logger.info("transaction_debit_reconciliation_tick_completed")
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="bill")
            logger.debug("transaction_debit_reconciliation_tick_skipped_lock_held")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="bill", exc=exc)
            logger.error("transaction_debit_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("transaction_debit_reconciliation_lock_release_failed", error=str(exc))


async def _run_bill_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "bill_reconciliation"
    interval_seconds = int(settings.bill_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("bill_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    consumer = consumers.bill_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("bill_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:bill_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
            await consumer.process_job({})
            _mark_loop_success(loop_name, domain="bill")
            logger.info("bill_reconciliation_tick_completed")
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="bill")
            logger.debug("bill_reconciliation_tick_skipped_lock_held")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="bill", exc=exc)
            logger.error("bill_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("bill_reconciliation_lock_release_failed", error=str(exc))


async def _run_transaction_debit_refund_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "transaction_debit_refund_reconciliation"
    interval_seconds = int(settings.refund_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("transaction_debit_refund_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    consumer = consumers.transaction_debit_refund_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("transaction_debit_refund_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:transaction_debit_refund_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
            await consumer.process_job({})
            _mark_loop_success(loop_name, domain="refund")
            logger.info("transaction_debit_refund_reconciliation_tick_completed")
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="refund")
            logger.debug("transaction_debit_refund_reconciliation_tick_skipped_lock_held")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="refund", exc=exc)
            logger.error("transaction_debit_refund_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("transaction_debit_refund_reconciliation_lock_release_failed", error=str(exc))


async def _run_payout_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "payout_reconciliation"
    interval_seconds = int(settings.payout_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("payout_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    payout_reconciliation_consumer = consumers.payout_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
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
            _mark_loop_lock_skipped(loop_name, domain="payout")
            logger.debug("payout_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="payout", exc=exc)
            logger.error("payout_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await payout_reconciliation_consumer.process_job({})
            _mark_loop_success(loop_name, domain="payout")
            logger.info("payout_reconciliation_tick_completed")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="payout", exc=exc)
            logger.error("payout_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("payout_reconciliation_lock_release_failed", error=str(exc))


async def _run_funding_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "funding_reconciliation"
    interval_seconds = int(settings.funding_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("funding_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    funding_reconciliation_consumer = consumers.funding_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("funding_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:funding_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="funding")
            logger.debug("funding_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="funding", exc=exc)
            logger.error("funding_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await funding_reconciliation_consumer.process_job({})
            _mark_loop_success(loop_name, domain="funding")
            logger.info("funding_reconciliation_tick_completed")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="funding", exc=exc)
            logger.error("funding_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("funding_reconciliation_lock_release_failed", error=str(exc))


async def _run_refund_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "refund_reconciliation"
    interval_seconds = int(settings.refund_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("refund_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    refund_reconciliation_consumer = consumers.refund_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("refund_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:refund_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="refund")
            logger.debug("refund_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="refund", exc=exc)
            logger.error("refund_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await refund_reconciliation_consumer.process_job({})
            _mark_loop_success(loop_name, domain="refund")
            logger.info("refund_reconciliation_tick_completed")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="refund", exc=exc)
            logger.error("refund_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("refund_reconciliation_lock_release_failed", error=str(exc))


async def _run_ledger_posting_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "ledger_posting_reconciliation"
    interval_seconds = int(settings.ledger_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("ledger_posting_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    ledger_consumer = consumers.ledger_posting_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("ledger_posting_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:ledger_posting_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="ledger")
            logger.debug("ledger_posting_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="ledger", exc=exc)
            logger.error("ledger_posting_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await ledger_consumer.process_job({})
            _mark_loop_success(loop_name, domain="ledger")
            logger.info("ledger_posting_reconciliation_tick_completed")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="ledger", exc=exc)
            logger.error("ledger_posting_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("ledger_posting_reconciliation_lock_release_failed", error=str(exc))


async def _run_ledger_exposure_reconciliation_loop(stop_event: asyncio.Event) -> None:
    loop_name = "ledger_exposure_reconciliation"
    interval_seconds = int(settings.ledger_reconciliation_interval_seconds or 0)
    if interval_seconds <= 0:
        _mark_loop_disabled(loop_name)
        logger.info("ledger_exposure_reconciliation_loop_disabled")
        return

    consumers = setup_transaction_worker_consumers()
    ledger_consumer = consumers.ledger_exposure_reconciliation
    lock_ttl_seconds = max(interval_seconds * 2, 60)
    _mark_loop_started(loop_name)
    logger.info("ledger_exposure_reconciliation_loop_started", interval_seconds=interval_seconds)

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass

        lock = RedisDistributedLock(
            RedisClient.get_client(),
            key=f"{settings.project_name}:ledger_exposure_reconciliation:{settings.runtime.infrastructure_environment}",
            ttl_seconds=lock_ttl_seconds,
        )
        try:
            await lock.acquire(wait_seconds=0.1)
        except RedisLockTimeoutError:
            _mark_loop_lock_skipped(loop_name, domain="ledger")
            logger.debug("ledger_exposure_reconciliation_tick_skipped_lock_held")
            continue
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="ledger", exc=exc)
            logger.error("ledger_exposure_reconciliation_lock_failed", error=str(exc), exc_info=True)
            continue

        try:
            await ledger_consumer.process_job({})
            _mark_loop_success(loop_name, domain="ledger")
            logger.info("ledger_exposure_reconciliation_tick_completed")
        except Exception as exc:
            _mark_loop_failure(loop_name, domain="ledger", exc=exc)
            logger.error("ledger_exposure_reconciliation_tick_failed", error=str(exc), exc_info=True)
        finally:
            try:
                await lock.release()
            except Exception as exc:
                logger.warning("ledger_exposure_reconciliation_lock_release_failed", error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown logic for the standalone transaction worker."""
    global _worker_task, _direct_transfer_reconciliation_task
    global _transaction_debit_reconciliation_task, _transaction_debit_refund_reconciliation_task
    global _funding_reconciliation_task, _bill_reconciliation_task, _payout_reconciliation_task
    global _refund_reconciliation_task, _ledger_posting_reconciliation_task
    global _ledger_exposure_reconciliation_task, _stop_event

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
        _ledger_posting_reconciliation_task = asyncio.create_task(
            _run_ledger_posting_reconciliation_loop(_stop_event),
            name="ledger-posting-reconciliation-loop",
        )
        _ledger_exposure_reconciliation_task = asyncio.create_task(
            _run_ledger_exposure_reconciliation_loop(_stop_event),
            name="ledger-exposure-reconciliation-loop",
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
    for task in (
        _direct_transfer_reconciliation_task,
        _transaction_debit_reconciliation_task,
        _bill_reconciliation_task,
        _transaction_debit_refund_reconciliation_task,
        _funding_reconciliation_task,
        _payout_reconciliation_task,
        _refund_reconciliation_task,
        _ledger_posting_reconciliation_task,
        _ledger_exposure_reconciliation_task,
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
    _ledger_posting_reconciliation_task = None
    _ledger_exposure_reconciliation_task = None
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
        "worker_enabled": settings.async_transport.lower() in {"aws", "redis"},
        "enabled_domains": _enabled_domain_flags(),
        "async_transport": settings.async_transport,
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
