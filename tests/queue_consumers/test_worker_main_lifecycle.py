import asyncio
from unittest.mock import AsyncMock

import pytest

import apps.core.src.worker_main as worker_main


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


@pytest.mark.asyncio
async def test_run_worker_starts_and_stops_stream_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    message_consumer = object()
    stream_consumer = object()
    loop_probe = _LoopProbe()
    stop_event = asyncio.Event()

    monkeypatch.setattr(worker_main, "warm_runtime", AsyncMock())
    monkeypatch.setattr(worker_main, "setup_core_consumers", lambda: (message_consumer, stream_consumer))
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
async def test_run_worker_stays_passive_when_chat_consumers_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    loop_probe = _LoopProbe()

    monkeypatch.setattr(worker_main, "warm_runtime", AsyncMock())
    monkeypatch.setattr(worker_main.settings, "enable_chat_consumers", False)
    monkeypatch.setattr(worker_main, "_run_stream_loop", loop_probe.run)
    setup_mock = AsyncMock()
    monkeypatch.setattr(worker_main, "setup_core_consumers", setup_mock)

    task = asyncio.create_task(worker_main.run_worker(stop_event=stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    worker_main.warm_runtime.assert_awaited_once()
    setup_mock.assert_not_called()
    assert loop_probe.started is False
