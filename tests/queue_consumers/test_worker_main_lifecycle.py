import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import apps.chat.src.worker_main as worker_main
from shared.cache.distributed_lock import RedisDistributedLock


class _LoopProbe:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False
        self.args: tuple[object, object] | None = None

    async def run(self, message_consumer: object, stream_consumer: object) -> None:
        self.started = True
        self.args = (message_consumer, stream_consumer)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _StreamConsumerStub:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str]] = []

    async def ack(self, stream_name: str, record_id: str) -> None:
        self.acked.append((stream_name, record_id))


class _RedisLockStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, _numkeys: int, key: str, token: str, *args: str) -> int:
        del args
        if self.values.get(key) != token:
            return 0
        if "del" in script:
            del self.values[key]
            return 1
        return 1


def _record(record_id: str, phone_number: str) -> worker_main.RedisStreamRecord:
    return worker_main.RedisStreamRecord(
        stream_name="chat:messages",
        record_id=record_id,
        topic="message.received",
        payload={
            "channel": "whatsapp",
            "phone_number": phone_number,
            "timestamp": "2099-01-01T00:00:00+00:00",
        },
    )


@pytest.mark.asyncio
async def test_run_worker_starts_and_stops_stream_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    message_consumer = object()
    stream_consumer = object()
    loop_probe = _LoopProbe()
    stop_event = asyncio.Event()

    monkeypatch.setattr(worker_main, "warm_runtime", AsyncMock())
    monkeypatch.setattr(worker_main, "setup_chat_consumers", lambda: (message_consumer, stream_consumer))
    monkeypatch.setattr(worker_main, "_run_stream_loop", loop_probe.run)

    task = asyncio.create_task(worker_main.run_worker(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    worker_main.warm_runtime.assert_awaited_once()
    assert loop_probe.started is True
    assert loop_probe.cancelled is True
    assert loop_probe.args == (message_consumer, stream_consumer)


@pytest.mark.asyncio
async def test_schedule_dispatcher_tick_runs_under_redis_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _RedisLockStub()
    dispatcher = SimpleNamespace(dispatch_due=AsyncMock(return_value={"processed": 1, "skipped": 0}))

    monkeypatch.setattr(worker_main.RedisClient, "get_client", lambda: redis)
    monkeypatch.setattr(worker_main, "setup_schedule_dispatcher", lambda: dispatcher)

    stats = await worker_main._dispatch_due_schedules_with_lock(lock_ttl_seconds=60)

    assert stats == {"processed": 1, "skipped": 0}
    dispatcher.dispatch_due.assert_awaited_once()
    assert redis.values == {}


@pytest.mark.asyncio
async def test_schedule_dispatcher_tick_skips_when_lock_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _RedisLockStub()
    redis.values[worker_main._schedule_dispatcher_lock_key()] = "other-worker"
    dispatcher = SimpleNamespace(dispatch_due=AsyncMock())

    monkeypatch.setattr(worker_main.RedisClient, "get_client", lambda: redis)
    monkeypatch.setattr(worker_main, "setup_schedule_dispatcher", lambda: dispatcher)

    stats = await worker_main._dispatch_due_schedules_with_lock(lock_ttl_seconds=60)

    assert stats is None
    dispatcher.dispatch_due.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_records_process_concurrently_with_bounded_limit() -> None:
    stream_consumer = _StreamConsumerStub()
    active = 0
    max_active = 0

    class _Consumer:
        async def process_record(self, topic: str, payload: dict[str, Any]) -> None:
            nonlocal active, max_active
            del topic, payload
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

    await worker_main._process_stream_records(
        _Consumer(),
        stream_consumer,
        [_record("1-0", "2348162511001"), _record("2-0", "2348162511002")],
        asyncio.Semaphore(2),
    )

    assert max_active == 2
    assert set(stream_consumer.acked) == {("chat:messages", "1-0"), ("chat:messages", "2-0")}


@pytest.mark.asyncio
async def test_same_thread_records_serialize_through_distributed_lock() -> None:
    stream_consumer = _StreamConsumerStub()
    redis = _RedisLockStub()
    active = 0
    max_active = 0

    class _Consumer:
        async def process_record(self, topic: str, payload: dict[str, Any]) -> None:
            nonlocal active, max_active
            del topic
            lock = RedisDistributedLock(
                redis,
                key=f"chat:thread-lock:{payload['channel']}:{payload['phone_number']}",
                ttl_seconds=120,
            )
            await lock.acquire(wait_seconds=1, retry_interval_seconds=0.001)
            try:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                active -= 1
            finally:
                await lock.release()

    await worker_main._process_stream_records(
        _Consumer(),
        stream_consumer,
        [_record("1-0", "2348162511001"), _record("2-0", "2348162511001")],
        asyncio.Semaphore(2),
    )

    assert max_active == 1
    assert set(stream_consumer.acked) == {("chat:messages", "1-0"), ("chat:messages", "2-0")}


@pytest.mark.asyncio
async def test_same_thread_records_preserve_batch_order() -> None:
    stream_consumer = _StreamConsumerStub()
    events: list[str] = []
    suppress_flags: list[bool] = []

    class _Consumer:
        async def process_record(self, topic: str, payload: dict[str, Any]) -> None:
            del topic
            record_id = str(payload["record_id"])
            events.append(f"start:{record_id}")
            suppress_flags.append(
                bool((payload.get("channel_metadata") or {}).get("_suppress_intermediate_input_prompt"))
            )
            await asyncio.sleep(0.02 if record_id == "1-0" else 0)
            events.append(f"finish:{record_id}")

    first = _record("1-0", "2348162511001")
    second = _record("2-0", "2348162511001")
    first.payload["record_id"] = first.record_id
    second.payload["record_id"] = second.record_id

    await worker_main._process_stream_records(
        _Consumer(),
        stream_consumer,
        [first, second],
        asyncio.Semaphore(2),
    )

    assert events == ["start:1-0", "finish:1-0", "start:2-0", "finish:2-0"]
    assert suppress_flags == [True, False]
    assert stream_consumer.acked == [("chat:messages", "1-0"), ("chat:messages", "2-0")]


@pytest.mark.asyncio
async def test_same_thread_records_halt_after_failure_to_preserve_order() -> None:
    stream_consumer = _StreamConsumerStub()
    seen: list[str] = []

    class _Consumer:
        async def process_record(self, topic: str, payload: dict[str, Any]) -> None:
            del topic
            seen.append(str(payload["record_id"]))
            if payload["record_id"] == "1-0":
                raise RuntimeError("boom")

    first = _record("1-0", "2348162511001")
    second = _record("2-0", "2348162511001")
    first.payload["record_id"] = first.record_id
    second.payload["record_id"] = second.record_id

    await worker_main._process_stream_records(
        _Consumer(),
        stream_consumer,
        [first, second],
        asyncio.Semaphore(2),
    )

    assert seen == ["1-0"]
    assert stream_consumer.acked == []
