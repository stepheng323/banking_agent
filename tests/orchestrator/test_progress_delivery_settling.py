"""Race-safety tests for opportunistic progress delivery."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressTracker
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery
from apps.chat.src.agent.orchestrator.graph.progress_lifecycle import ProgressDeliveryRun, stop_progress_delivery
from banking.messaging.delivery.models import DeliveryAttemptResult


def _delivery() -> OrchestratorProgressDelivery:
    return OrchestratorProgressDelivery(SimpleNamespace())


def _make_progress_immediately_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.should_emit_progress", lambda snapshot: True
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.seconds_until_progress_eligible", lambda snapshot: 0.0
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.next_progress_delay_seconds",
        lambda stage_key, progress_count: 0.0,
    )


async def _run_delivery(
    tracker: TurnProgressTracker,
    delivery: OrchestratorProgressDelivery,
) -> None:
    await delivery.run_updates(
        tracker=tracker,
        phone_number="2348000000000",
        channel="telegram",
        channel_identity="123",
        inbound_message_id="tg.progress",
        thread_id="telegram:2348000000000",
        turn_id="tg.progress",
        enable_initial_typing=False,
    )


@pytest.mark.asyncio
async def test_completion_during_settling_suppresses_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_progress_immediately_eligible(monkeypatch)
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.05
    )
    sent: list[dict[str, object]] = []

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        del args
        sent.append(dict(kwargs))
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")

    task = asyncio.create_task(_run_delivery(tracker, _delivery()))
    await asyncio.sleep(0.01)
    await tracker.mark_turn_complete()
    await asyncio.wait_for(task, timeout=0.2)

    assert sent == []
    assert (await tracker.snapshot()).completed is True


@pytest.mark.asyncio
async def test_long_running_visible_stage_sends_one_settled_progress_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_progress_immediately_eligible(monkeypatch)
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.02
    )
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.MAX_PROGRESS_MESSAGES", 1)
    sent: list[dict[str, object]] = []

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        del args
        sent.append(dict(kwargs))
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")

    await asyncio.wait_for(_run_delivery(tracker, _delivery()), timeout=0.2)

    assert len(sent) == 1
    assert sent[0]["metadata"] == {
        "dedupe_key": "telegram:2348000000000:tg.progress:progress:0:query.fetching_transactions",
        "progress_stage": "query.fetching_transactions",
        "progress_count": 0,
        "progress_turn_id": "tg.progress",
        "inbound_message_id": "tg.progress",
    }
    assert (await tracker.snapshot()).progress_count == 1


@pytest.mark.asyncio
async def test_stage_change_during_settling_replaces_stale_progress_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_progress_immediately_eligible(monkeypatch)
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.04
    )
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.MAX_PROGRESS_MESSAGES", 1)
    sent: list[dict[str, object]] = []

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        del args
        sent.append(dict(kwargs))
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")
    task = asyncio.create_task(_run_delivery(tracker, _delivery()))

    await asyncio.sleep(0.01)
    await tracker.set_stage("query.comparing_periods")
    await asyncio.wait_for(task, timeout=0.25)

    assert [entry["metadata"]["progress_stage"] for entry in sent] == ["query.comparing_periods"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "deduped_completed"])
async def test_unsent_delivery_outcomes_do_not_poll_tightly(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    _make_progress_immediately_eligible(monkeypatch)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.MAX_PROGRESS_MESSAGES", 1)
    calls = 0

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        nonlocal calls
        del args, kwargs
        calls += 1
        return DeliveryAttemptResult(status=status)  # type: ignore[arg-type]

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")

    await asyncio.wait_for(_run_delivery(tracker, _delivery()), timeout=0.2)

    assert calls == 1
    assert (await tracker.snapshot()).progress_count == (1 if status != "failed" else 0)


@pytest.mark.asyncio
async def test_completion_does_not_wait_for_an_inflight_progress_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_progress_immediately_eligible(monkeypatch)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        del args, kwargs
        started.set()
        await release.wait()
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")
    task = asyncio.create_task(_run_delivery(tracker, _delivery()))
    await asyncio.wait_for(started.wait(), timeout=0.2)

    snapshot = await asyncio.wait_for(
        stop_progress_delivery(ProgressDeliveryRun(tracker=tracker, task=task)), timeout=0.2
    )

    assert snapshot.completed is True
    release.set()


@pytest.mark.asyncio
async def test_completion_before_progress_is_eligible_emits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, object]] = []

    async def _enqueue(*args: object, **kwargs: object) -> DeliveryAttemptResult:
        del args
        sent.append(dict(kwargs))
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue)
    tracker = TurnProgressTracker(locale="en")
    await tracker.set_stage("query.fetching_transactions")
    await tracker.mark_turn_complete()

    await asyncio.wait_for(_run_delivery(tracker, _delivery()), timeout=0.1)

    assert sent == []
