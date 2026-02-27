import asyncio
from unittest.mock import AsyncMock

import pytest

import apps.core.src.worker_main as worker_main


class _BlockingConsumer:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.cancelled = False

    async def start(self) -> None:
        self.start_calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_run_worker_starts_and_stops_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    message_consumer = _BlockingConsumer()
    flow_event_consumer = _BlockingConsumer()
    stop_event = asyncio.Event()

    monkeypatch.setattr(worker_main, "warm_runtime", AsyncMock())
    monkeypatch.setattr(worker_main, "setup_core_consumers", lambda: (message_consumer, flow_event_consumer))

    task = asyncio.create_task(worker_main.run_worker(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    assert message_consumer.start_calls == 1
    assert flow_event_consumer.start_calls == 1
    assert message_consumer.stop_calls == 1
    assert flow_event_consumer.stop_calls == 1
    assert message_consumer.cancelled is True
    assert flow_event_consumer.cancelled is True
