import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.core.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from apps.core.src.agent.orchestrator.progress import TurnProgressSnapshot


class _CheckpointerStub:
    async def asetup(self) -> None:
        return

    async def adelete_thread(self, thread_id: str) -> None:
        del thread_id
        return


class _GraphStub:
    def __init__(self) -> None:
        self.last_inputs: dict | None = None

    async def ainvoke(self, inputs: dict, config: dict) -> dict:
        del config
        self.last_inputs = inputs
        return {
            "outbox": [],
            "final_response": None,
            "loaded_context": {"language": "en"},
        }


class _ContextManagerStub:
    async def load_context_parallel(self, phone_number: str):
        del phone_number
        return (
            {
                "profile": {"id": str(uuid4())},
                "accounts": [],
                "beneficiaries": [],
                "history": [],
                "language": "en",
            },
            None,
            None,
            None,
        )


class _ProgressTrackerStub:
    def __init__(self, *, progress_count: int, last_progress_sent_at: float | None) -> None:
        self.progress_count = progress_count
        self.last_progress_sent_at = last_progress_sent_at

    async def snapshot(self) -> TurnProgressSnapshot:
        return TurnProgressSnapshot(
            stage_key="query.fetching_transactions",
            started_at=0.0,
            stage_started_at=0.0,
            last_progress_sent_at=self.last_progress_sent_at,
            progress_count=self.progress_count,
            stage_metadata={"scope_label": "what you sent to mum"},
            locale="en",
        )

    async def wait_for_update(self, timeout_seconds: float | None = None) -> None:
        del timeout_seconds
        await asyncio.sleep(0)

    async def record_progress_sent(self) -> None:
        self.progress_count += 1

    async def set_stage(self, stage_key: str, *, stage_metadata: dict | None = None) -> None:
        del stage_key, stage_metadata
        return


@pytest.mark.asyncio
async def test_graph_handler_invoke_passes_quoted_message_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )

    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    await handler.invoke(
        MessageContext(
            phone_number="2348000000001",
            text="this failed transfer",
            message_id="wamid.11",
            quoted_message_id="wamid.receipt.42",
            channel="whatsapp",
            channel_identity="2348000000001",
        )
    )

    assert graph.last_inputs is not None
    assert graph.last_inputs["quoted_message_id"] == "wamid.receipt.42"
    assert graph.last_inputs["has_quote"] is True


@pytest.mark.asyncio
async def test_graph_handler_resume_flow_clears_quoted_message_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )

    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    await handler.resume_flow(
        phone_number="2348000000001",
        payload={"pin_verified": True, "flow_type": "transfer"},
        channel="whatsapp",
    )

    assert graph.last_inputs is not None
    assert graph.last_inputs["quoted_message_id"] is None
    assert graph.last_inputs["has_quote"] is False


@pytest.mark.asyncio
async def test_graph_handler_logs_semantic_path_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SemanticGraphStub(_GraphStub):
        async def ainvoke(self, inputs: dict, config: dict) -> dict:
            del inputs, config
            return {
                "outbox": [{"type": "say", "text": "linked and pending"}],
                "final_response": "linked and pending",
                "loaded_context": {"language": "en"},
                "semantic_path_shape": "turn_router_only",
                "fast_path_triggered": True,
            }

    graph = _SemanticGraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.handler.logger.info", _capture)

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )
    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    await handler.invoke(
        MessageContext(
            phone_number="2348000000001",
            text="Can I use First Bank now?",
            message_id="wamid.12",
            channel="whatsapp",
            channel_identity="2348000000001",
        )
    )

    assert ("orchestrator_semantic_path", {"semantic_path_shape": "turn_router_only", "path_label": "fast_path", "phone_number": "2348000000001"}) in events


@pytest.mark.asyncio
async def test_graph_handler_attaches_delivery_metadata_after_visible_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.TurnProgressTracker",
        lambda locale: _ProgressTrackerStub(progress_count=1, last_progress_sent_at=asyncio.get_running_loop().time()),
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )
    handler._cleanup_if_idle = AsyncMock()
    handler._apply_session_ttl = AsyncMock()

    result = await handler.invoke(
        MessageContext(
            phone_number="2348000000002",
            text="What about last week",
            message_id="wamid.22",
            channel="whatsapp",
            channel_identity="2348000000002",
        )
    )

    assert result["delivery_metadata"] == {"suppress_typing_indicator": True}


@pytest.mark.asyncio
async def test_progress_update_finishes_when_progress_task_is_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.core.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.handler.should_emit_progress", lambda snapshot: True)
    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.handler.seconds_until_progress_eligible", lambda snapshot: 0.0)
    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.handler.next_progress_delay_seconds", lambda progress_count: 0.0)

    delivery_events: list[str] = []

    async def _enqueue_outbox_say(*args, **kwargs) -> None:
        del args, kwargs
        delivery_events.append("started")
        await asyncio.sleep(0.01)
        delivery_events.append("finished")

    monkeypatch.setattr("apps.core.src.agent.orchestrator.graph.handler.enqueue_outbox_say", _enqueue_outbox_say)

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=_ContextManagerStub(),
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )

    tracker = _ProgressTrackerStub(progress_count=0, last_progress_sent_at=None)
    progress_task = asyncio.create_task(
        handler._run_progress_updates(
            tracker=tracker,
            phone_number="2348000000003",
            channel="whatsapp",
            channel_identity="2348000000003",
            inbound_message_id="wamid.33",
            thread_id="whatsapp:2348000000003",
        )
    )
    await asyncio.sleep(0)
    progress_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await progress_task

    assert delivery_events == ["started", "finished"]
