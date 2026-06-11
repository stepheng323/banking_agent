import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from apps.receipt import main as receipt_main
from apps.transaction import main as transaction_main
from apps.transaction.dependencies import TransactionWorkerConsumers
from shared.queue.redis_stream_consumer import RedisStreamRecord


class _StreamConsumerStub:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str]] = []

    async def ack(self, stream_name: str, record_id: str) -> None:
        self.acked.append((stream_name, record_id))


class _TimedLoopStreamStub:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.stream_names = ["async:test"]
        self.claim_calls = 0
        self.block_values: list[int] = []

    async def ensure_groups(self) -> None:
        pass

    async def claim_stale(self, *, min_idle_ms: int, count: int) -> list[RedisStreamRecord]:
        assert min_idle_ms == 60_000
        assert count == 25
        self.claim_calls += 1
        return []

    async def consume(self, *, count: int, block_ms: int) -> list[RedisStreamRecord]:
        assert count == 25
        self.block_values.append(block_ms)
        if len(self.block_values) >= 3:
            raise asyncio.CancelledError
        return []


class _LoggerStub:
    def __init__(self) -> None:
        self.debugs: list[tuple[str, dict[str, object]]] = []

    def debug(self, event: str, **fields: object) -> None:
        self.debugs.append((event, fields))


def _record(topic: str, payload: dict | None = None) -> RedisStreamRecord:
    return RedisStreamRecord(
        stream_name="async:test",
        record_id="1-0",
        topic=topic,
        payload=payload or {},
    )


@pytest.mark.asyncio
async def test_transaction_worker_lifespan_starts_with_runtime_status_log(monkeypatch: pytest.MonkeyPatch) -> None:
    async def idle_loop(stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    for loop_name in (
        "_run_transaction_stream_worker",
        "_run_funding_reconciliation_loop",
        "_run_direct_transfer_reconciliation_loop",
        "_run_transaction_debit_reconciliation_loop",
        "_run_bill_reconciliation_loop",
        "_run_transaction_debit_refund_reconciliation_loop",
        "_run_payout_reconciliation_loop",
        "_run_refund_reconciliation_loop",
        "_run_ledger_reconciliation_loop",
    ):
        monkeypatch.setattr(transaction_main, loop_name, idle_loop)

    async with transaction_main.lifespan(transaction_main.app):
        assert transaction_main._worker_task is not None


@pytest.mark.asyncio
async def test_ledger_reconciliation_tick_runs_posting_before_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class _Lock:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def acquire(self, *, wait_seconds: float) -> None:
            del wait_seconds

        async def release(self) -> None:
            pass

    class _Consumer:
        def __init__(self, name: str) -> None:
            self.name = name

        async def process_job(self, payload: dict[str, object]) -> None:
            del payload
            events.append(self.name)

    monkeypatch.setattr(transaction_main.RedisClient, "get_client", lambda: object())
    monkeypatch.setattr(transaction_main, "RedisDistributedLock", _Lock)

    consumers = cast(
        TransactionWorkerConsumers,
        SimpleNamespace(
            ledger_posting_reconciliation=_Consumer("posting"),
            ledger_exposure_reconciliation=_Consumer("exposure"),
        ),
    )

    await transaction_main._run_ledger_reconciliation_tick(consumers, lock_ttl_seconds=60)

    assert events == ["posting", "exposure"]


@pytest.mark.asyncio
async def test_ledger_reconciliation_tick_skips_exposure_when_posting_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Lock:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def acquire(self, *, wait_seconds: float) -> None:
            del wait_seconds
            raise transaction_main.RedisLockTimeoutError("posting already running")

        async def release(self) -> None:
            pass

    class _Consumer:
        def __init__(self, name: str) -> None:
            self.name = name

        async def process_job(self, payload: dict[str, object]) -> None:
            del payload
            events.append(self.name)

    monkeypatch.setattr(transaction_main.RedisClient, "get_client", lambda: object())
    monkeypatch.setattr(transaction_main, "RedisDistributedLock", _Lock)

    consumers = cast(
        TransactionWorkerConsumers,
        SimpleNamespace(
            ledger_posting_reconciliation=_Consumer("posting"),
            ledger_exposure_reconciliation=_Consumer("exposure"),
        ),
    )

    await transaction_main._run_ledger_reconciliation_tick(consumers, lock_ttl_seconds=60)

    assert events == []


@pytest.mark.asyncio
async def test_transaction_stream_worker_claims_stale_on_timer_and_uses_configured_block_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _TimedLoopStreamStub()
    times = iter([0.0, 10.0, 20.0])

    monkeypatch.setattr(transaction_main, "time", SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(transaction_main.settings, "transaction_worker_stream_block_ms", 2345)
    monkeypatch.setattr(transaction_main, "RedisStreamConsumer", lambda *args, **kwargs: stream)
    monkeypatch.setattr(transaction_main, "setup_transaction_worker_consumers", lambda: SimpleNamespace())

    with pytest.raises(asyncio.CancelledError):
        await transaction_main._run_transaction_stream_worker(asyncio.Event())

    assert stream.claim_calls == 1
    assert stream.block_values == [2345, 2345, 2345]


def test_transaction_worker_success_and_lock_skip_logs_are_debug_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _LoggerStub()
    operational_events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_emit_operational_event(*args: object, **kwargs: object) -> None:
        operational_events.append((args, kwargs))

    monkeypatch.setattr(transaction_main, "logger", logger)
    monkeypatch.setattr(transaction_main, "emit_operational_event", fake_emit_operational_event)

    transaction_main._mark_loop_success("ledger_posting_reconciliation", domain="ledger")
    transaction_main._mark_loop_lock_skipped("ledger_posting_reconciliation", domain="ledger")

    assert operational_events == []
    assert logger.debugs == [
        ("ledger_posting_reconciliation_tick_completed", {"domain": "ledger"}),
        ("ledger_posting_reconciliation_tick_skipped_lock_held", {"domain": "ledger"}),
    ]


def test_transaction_worker_failure_remains_operational_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _LoggerStub()
    operational_events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_emit_operational_event(*args: object, **kwargs: object) -> None:
        operational_events.append((args, kwargs))

    monkeypatch.setattr(transaction_main, "logger", logger)
    monkeypatch.setattr(transaction_main, "emit_operational_event", fake_emit_operational_event)

    transaction_main._mark_loop_failure("ledger_posting_reconciliation", domain="ledger", exc=RuntimeError("boom"))

    assert len(operational_events) == 1
    args, kwargs = operational_events[0]
    assert args == ("ledger_posting_reconciliation_tick_failed",)
    assert kwargs["severity"] == "high"
    assert kwargs["domain"] == "ledger"
    assert kwargs["details"] == {"error_type": "RuntimeError"}
    assert kwargs["logger"] is logger


@pytest.mark.asyncio
async def test_receipt_stream_worker_claims_stale_on_timer_and_uses_configured_block_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _TimedLoopStreamStub()
    times = iter([0.0, 10.0, 20.0])

    monkeypatch.setattr(receipt_main, "time", SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(receipt_main.settings, "receipt_worker_stream_block_ms", 3456)
    monkeypatch.setattr(receipt_main, "RedisStreamConsumer", lambda *args, **kwargs: stream)
    monkeypatch.setattr(receipt_main, "setup_receipt_worker_consumers", lambda: (object(), object()))

    with pytest.raises(asyncio.CancelledError):
        await receipt_main._run_receipt_stream_worker(asyncio.Event())

    assert stream.claim_calls == 1
    assert stream.block_values == [3456, 3456, 3456]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "topic",
    [
        "transaction.execute",
        "transaction_debit.process",
        "transaction_debit.reconcile",
        "transaction_debit.refund",
        "transaction_debit.refund_reconcile",
        "direct_transfer.reconcile",
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
    ],
)
async def test_transaction_worker_routes_by_stream_topic(topic: str) -> None:
    transaction = type("TransactionConsumer", (), {"process_transaction": AsyncMock()})()
    transaction_debit = type("TransactionDebitConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_reconcile = type("TransactionDebitReconciliationConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_refund = type("TransactionDebitRefundConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_refund_reconcile = type(
        "TransactionDebitRefundReconciliationConsumer", (), {"process_job": AsyncMock()}
    )()
    direct_transfer_reconcile = type("DirectTransferReconciliationConsumer", (), {"process_job": AsyncMock()})()
    funding = type("FundingConsumer", (), {"process_job": AsyncMock()})()
    funding_reconcile = type("FundingReconciliationConsumer", (), {"process_job": AsyncMock()})()
    bill_fulfill = type("BillFulfillmentConsumer", (), {"process_job": AsyncMock()})()
    bill_reconcile = type("BillReconciliationConsumer", (), {"process_job": AsyncMock()})()
    payout = type("PayoutConsumer", (), {"process_job": AsyncMock()})()
    payout_reconcile = type("PayoutReconciliationConsumer", (), {"process_job": AsyncMock()})()
    refund = type("RefundConsumer", (), {"process_job": AsyncMock()})()
    refund_reconcile = type("RefundReconciliationConsumer", (), {"process_job": AsyncMock()})()
    ledger_posting_reconcile = type("LedgerPostingReconciliationConsumer", (), {"process_job": AsyncMock()})()
    ledger_exposure_reconcile = type("LedgerExposureReconciliationConsumer", (), {"process_job": AsyncMock()})()
    deps = TransactionWorkerConsumers(
        transaction=transaction,
        transaction_debit=transaction_debit,
        transaction_debit_reconciliation=transaction_debit_reconcile,
        transaction_debit_refund=transaction_debit_refund,
        transaction_debit_refund_reconciliation=transaction_debit_refund_reconcile,
        direct_transfer_reconciliation=direct_transfer_reconcile,
        funding=funding,
        bill_fulfillment=bill_fulfill,
        bill_reconciliation=bill_reconcile,
        payout=payout,
        payout_reconciliation=payout_reconcile,
        refund=refund,
        funding_reconciliation=funding_reconcile,
        refund_reconciliation=refund_reconcile,
        ledger_posting_reconciliation=ledger_posting_reconcile,
        ledger_exposure_reconciliation=ledger_exposure_reconcile,
    )
    stream_consumer = _StreamConsumerStub()
    payload = {"job_id": "job-1"}

    await transaction_main._process_stream_record(deps, stream_consumer, _record(topic, payload))

    expected_target = {
        "transaction.execute": transaction.process_transaction,
        "transaction_debit.process": transaction_debit.process_job,
        "transaction_debit.reconcile": transaction_debit_reconcile.process_job,
        "transaction_debit.refund": transaction_debit_refund.process_job,
        "transaction_debit.refund_reconcile": transaction_debit_refund_reconcile.process_job,
        "direct_transfer.reconcile": direct_transfer_reconcile.process_job,
        "funding.process": funding.process_job,
        "funding.reconcile": funding_reconcile.process_job,
        "bill.fulfill": bill_fulfill.process_job,
        "bill.reconcile": bill_reconcile.process_job,
        "payout.process": payout.process_job,
        "payout.reconcile": payout_reconcile.process_job,
        "refund.process": refund.process_job,
        "refund.reconcile": refund_reconcile.process_job,
        "ledger.reconcile.postings": ledger_posting_reconcile.process_job,
        "ledger.reconcile.exposure": ledger_exposure_reconcile.process_job,
    }[topic]
    expected_target.assert_awaited_once_with(payload)
    assert stream_consumer.acked == [("async:test", "1-0")]


@pytest.mark.asyncio
async def test_receipt_worker_routes_receipt_stream_topic() -> None:
    receipt = type("ReceiptConsumer", (), {"process_job": AsyncMock()})()
    notification = type("NotificationConsumer", (), {"process_job": AsyncMock()})()
    stream_consumer = _StreamConsumerStub()

    await receipt_main._process_stream_record((receipt, notification), stream_consumer, _record("receipt.process"))

    receipt.process_job.assert_awaited_once_with({})
    notification.process_job.assert_not_awaited()
    assert stream_consumer.acked == [("async:test", "1-0")]


@pytest.mark.asyncio
async def test_receipt_worker_routes_notification_stream_topic() -> None:
    receipt = type("ReceiptConsumer", (), {"process_job": AsyncMock()})()
    notification = type("NotificationConsumer", (), {"process_job": AsyncMock()})()
    stream_consumer = _StreamConsumerStub()

    await receipt_main._process_stream_record((receipt, notification), stream_consumer, _record("notification.send"))

    receipt.process_job.assert_not_awaited()
    notification.process_job.assert_awaited_once_with({})
    assert stream_consumer.acked == [("async:test", "1-0")]
