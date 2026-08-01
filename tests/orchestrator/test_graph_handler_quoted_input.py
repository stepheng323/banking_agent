import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from apps.chat.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.chat.src.agent.orchestrator.graph.progress import MAX_PROGRESS_MESSAGES, TurnProgressSnapshot
from apps.chat.src.agent.orchestrator.graph.progress_delivery import OrchestratorProgressDelivery
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.models.turn_directive import TurnOutcomeKind, build_turn_directive
from banking.messaging.delivery.models import DeliveryAttemptResult
from shared.config.settings import settings


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
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def load_context_parallel(
        self,
        phone_number: str,
        *,
        path_label: str = "planner_path",
        user: object | None = None,
        profile_mode: str = "full",
        account_mode: str = "full",
        beneficiary_mode: str = "full",
    ):
        self.calls.append(
            {
                "phone_number": phone_number,
                "path_label": path_label,
                "user": user,
                "profile_mode": profile_mode,
                "account_mode": account_mode,
                "beneficiary_mode": beneficiary_mode,
            }
        )
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


class _PublisherStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def publish(self, topic: str, message: dict[str, object]) -> None:
        self.calls.append({"topic": topic, "message": message})


class _NoopDistributedLock:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def acquire(self, *, wait_seconds: float, retry_interval_seconds: float = 0.1) -> bool:
        del wait_seconds, retry_interval_seconds
        return True

    async def release(self) -> bool:
        return True

    async def renew_periodically(self, *, interval_seconds: float) -> None:
        del interval_seconds
        while True:
            await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _stub_distributed_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.thread_lock.RedisDistributedLock",
        _NoopDistributedLock,
    )


@pytest.mark.asyncio
async def test_graph_handler_invoke_passes_quoted_message_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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

    handler.housekeeping.run = AsyncMock(return_value=None)

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
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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

    handler.housekeeping.run = AsyncMock(return_value=None)

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
                "turn_directive": build_turn_directive(
                    owner="semantic_router",
                    decision="direct_response",
                    outcome_kind=TurnOutcomeKind.DIRECT_RESPONSE,
                    path_shape="semantic_router_direct",
                ),
            }

    graph = _SemanticGraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.handler.logger.info", _capture)
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", True)

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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
    handler.housekeeping.run = AsyncMock(return_value=None)

    await handler.invoke(
        MessageContext(
            phone_number="2348000000001",
            text="Can I use First Bank now?",
            message_id="wamid.12",
            channel="whatsapp",
            channel_identity="2348000000001",
        )
    )

    assert (
        "orchestrator_semantic_path",
        {"semantic_path_shape": "semantic_router_direct", "path_label": "direct_path", "phone_number": "2348000000001"},
    ) in events


@pytest.mark.asyncio
async def test_graph_handler_uses_lightweight_hydration_for_fresh_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    context_manager = _ContextManagerStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=context_manager,
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )
    handler.housekeeping.run = AsyncMock(return_value=None)

    resolved_user = SimpleNamespace(id="user-1")
    await handler.invoke(
        MessageContext(
            phone_number="2348000000001",
            text="send 10k to mum",
            message_id="wamid.transfer.1",
            channel="telegram",
            channel_identity="927331985",
            resolved_user=resolved_user,
        )
    )

    assert context_manager.calls == [
        {
            "phone_number": "2348000000001",
            "path_label": "direct_path",
            "user": resolved_user,
            "profile_mode": "minimal",
            "account_mode": "full",
            "beneficiary_mode": "cache_only",
        }
    ]


@pytest.mark.asyncio
async def test_graph_handler_cancel_prefastpath_uses_cache_only_hydration(monkeypatch: pytest.MonkeyPatch) -> None:
    class _CancelGraphStub(_GraphStub):
        async def ainvoke(self, inputs: dict, config: dict) -> dict:
            del config
            self.last_inputs = inputs
            return {
                "outbox": [],
                "final_response": "Transaction cancelled.",
                "loaded_context": {"language": "en"},
                "direct_path_triggered": True,
            }

    graph = _CancelGraphStub()
    context_manager = _ContextManagerStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=context_manager,
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )
    handler.housekeeping.run = AsyncMock(return_value=None)

    await handler.invoke(
        MessageContext(
            phone_number="2348000000008",
            text="please cancel",
            message_id="wamid.cancel.1",
            channel="whatsapp",
            channel_identity="2348000000008",
        )
    )

    assert context_manager.calls == [
        {
            "phone_number": "2348000000008",
            "path_label": "cancel_path",
            "user": None,
            "profile_mode": "minimal",
            "account_mode": "cache_only",
            "beneficiary_mode": "cache_only",
        }
    ]


@pytest.mark.asyncio
async def test_graph_handler_meta_prefastpath_uses_minimal_hydration_and_skips_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    context_manager = _ContextManagerStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
        support_service=SimpleNamespace(),
        faq_service=SimpleNamespace(),
        user_repo=SimpleNamespace(),
        beneficiary_repo=SimpleNamespace(),
        account_repo=SimpleNamespace(),
        actionable_message_repo=SimpleNamespace(),
        banking_provider=SimpleNamespace(),
        context_manager=context_manager,
        redis_client=SimpleNamespace(),
        publisher=SimpleNamespace(),
        beneficiary_suggestion_service=SimpleNamespace(),
    )
    handler.housekeeping.run = AsyncMock(return_value=None)

    await handler.invoke(
        MessageContext(
            phone_number="2348000000009",
            text="what can you do",
            message_id="wamid.meta.1",
            channel="telegram",
            channel_identity="927331985",
        )
    )

    assert context_manager.calls == [
        {
            "phone_number": "2348000000009",
            "path_label": "direct_path",
            "user": None,
            "profile_mode": "minimal",
            "account_mode": "cache_only",
            "beneficiary_mode": "cache_only",
        }
    ]


@pytest.mark.asyncio
async def test_graph_handler_logs_route_metrics_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RouteMetricsGraphStub(_GraphStub):
        async def ainvoke(self, inputs: dict, config: dict) -> dict:
            del inputs, config
            return {
                "outbox": [],
                "final_response": "checking",
                "loaded_context": {"language": "en"},
                "turn_directive": build_turn_directive(
                    owner="semantic_router",
                    decision="domain_query",
                    target_domain="query",
                    mode="continuation",
                    outcome_kind=TurnOutcomeKind.TASK_DISPATCH,
                    path_shape="semantic_router_domain",
                ),
                "planner_used": False,
                "preplanner_expected_transaction_executors": [],
            }

    graph = _RouteMetricsGraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )

    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.handler.logger.info", _capture)
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", True)

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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
    handler.housekeeping.run = AsyncMock(return_value=None)

    await handler.invoke(
        MessageContext(
            phone_number="2348000000007",
            text="How much total",
            message_id="wamid.77",
            channel="whatsapp",
            channel_identity="2348000000007",
        )
    )

    route_metrics = next(payload for event, payload in events if event == "orchestrator_route_metrics")
    assert route_metrics["phone_number"] == "2348000000007"
    assert route_metrics["path_label"] == "direct_path"
    assert route_metrics["semantic_path_shape"] == "semantic_router_domain"
    assert route_metrics["routing_owner"] == "semantic_router"
    assert route_metrics["routing_decision"] == "domain_query"
    assert route_metrics["routing_target_domain"] == "query"
    assert route_metrics["routing_mode"] == "continuation"
    assert route_metrics["planner_used"] is False
    assert route_metrics["planner_primary_intent"] is None
    assert route_metrics["direct_path_triggered"] is True
    assert route_metrics["expected_transaction_executors"] == []
    assert route_metrics["task_executors"] == []
    assert route_metrics["task_count"] == 0
    assert route_metrics["wave_count"] == 0
    assert route_metrics["progress_count"] == 0
    assert route_metrics["total_duration_ms"] == pytest.approx(0, abs=5000)


@pytest.mark.asyncio
async def test_graph_handler_keeps_delivery_metadata_empty_after_visible_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.TurnProgressTracker",
        lambda locale: _ProgressTrackerStub(progress_count=1, last_progress_sent_at=asyncio.get_running_loop().time()),
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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
    handler.housekeeping.run = AsyncMock(return_value=None)

    result = await handler.invoke(
        MessageContext(
            phone_number="2348000000002",
            text="What about last week",
            message_id="wamid.22",
            channel="whatsapp",
            channel_identity="2348000000002",
        )
    )

    assert result["delivery_metadata"] == {}


@pytest.mark.asyncio
async def test_graph_handler_keeps_delivery_metadata_empty_without_visible_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.TurnProgressTracker",
        lambda locale: _ProgressTrackerStub(progress_count=0, last_progress_sent_at=None),
    )

    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.handler.logger.info", _capture)
    monkeypatch.setattr(settings, "orchestrator_verbose_logs", True)
    monkeypatch.setattr(settings.whatsapp, "typing_indicator_delay_ms", 650)

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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
    handler.housekeeping.run = AsyncMock(return_value=None)

    result = await handler.invoke(
        MessageContext(
            phone_number="2348000000005",
            text="What about last week",
            message_id="wamid.55",
            channel="whatsapp",
            channel_identity="2348000000005",
        )
    )

    assert result["delivery_metadata"] == {}
    assert (
        "orchestrator_progress_delivery_summary",
        {
            "progress_stage": "query.fetching_transactions",
            "progress_count": 0,
            "visible_progress_sent": False,
            "typing_policy": "consumer_owned_initial_typing",
            "preflight_initial_typing_enabled": True,
            "typing_visibility_delay_ms": 650,
        },
    ) in events


@pytest.mark.asyncio
async def test_progress_delivery_does_not_publish_initial_typing_when_flag_is_true() -> None:
    publisher = _PublisherStub()
    progress_delivery = OrchestratorProgressDelivery(publisher)
    tracker = _ProgressTrackerStub(progress_count=MAX_PROGRESS_MESSAGES, last_progress_sent_at=None)

    await progress_delivery.run_updates(
        tracker=tracker,
        phone_number="2348000000003",
        channel="whatsapp",
        channel_identity="2348000000003",
        inbound_message_id="wamid.no-typing",
        thread_id="whatsapp:2348000000003",
        turn_id="wamid.no-typing",
        enable_initial_typing=True,
    )

    assert publisher.calls == []


@pytest.mark.asyncio
async def test_progress_update_does_not_block_when_progress_task_is_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
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
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)

    delivery_events: list[str] = []

    async def _enqueue_outbox_say(*args, **kwargs) -> None:
        del args, kwargs
        delivery_events.append("started")
        await asyncio.sleep(0.01)
        delivery_events.append("finished")
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue_outbox_say
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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
        handler.progress_delivery.run_updates(
            tracker=tracker,
            phone_number="2348000000003",
            channel="whatsapp",
            channel_identity="2348000000003",
            inbound_message_id="wamid.33",
            thread_id="whatsapp:2348000000003",
            turn_id="wamid.33",
            enable_initial_typing=True,
        )
    )
    await asyncio.sleep(0)
    progress_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await progress_task

    assert delivery_events == ["started"]


@pytest.mark.asyncio
async def test_progress_task_waits_through_non_visible_stage_until_visible_stage_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
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
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)

    delivery_events: list[str] = []

    async def _enqueue_outbox_say(*args, **kwargs) -> DeliveryAttemptResult:
        del args, kwargs
        delivery_events.append("sent")
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue_outbox_say
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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

    class _StageFlippingTracker:
        def __init__(self) -> None:
            self._snapshots = [
                TurnProgressSnapshot(
                    stage_key="query.resolving_followup",
                    started_at=0.0,
                    stage_started_at=0.0,
                    last_progress_sent_at=None,
                    progress_count=0,
                    stage_metadata={"scope_label": "what you sent to mum"},
                    locale="en",
                ),
                TurnProgressSnapshot(
                    stage_key="query.fetching_transactions",
                    started_at=0.0,
                    stage_started_at=0.0,
                    last_progress_sent_at=None,
                    progress_count=0,
                    stage_metadata={"scope_label": "what you sent to mum"},
                    locale="en",
                ),
            ]
            self._index = 0
            self._progress_count = 0

        async def snapshot(self) -> TurnProgressSnapshot:
            snapshot = self._snapshots[self._index]
            return TurnProgressSnapshot(
                stage_key=snapshot.stage_key,
                started_at=snapshot.started_at,
                stage_started_at=snapshot.stage_started_at,
                last_progress_sent_at=snapshot.last_progress_sent_at,
                progress_count=self._progress_count,
                stage_metadata=snapshot.stage_metadata,
                locale=snapshot.locale,
            )

        async def wait_for_update(self, timeout_seconds: float | None = None) -> None:
            del timeout_seconds
            self._index = 1
            await asyncio.sleep(0)

        async def record_progress_sent(self) -> None:
            self._index = 1
            self._progress_count = MAX_PROGRESS_MESSAGES

    tracker = _StageFlippingTracker()
    progress_task = asyncio.create_task(
        handler.progress_delivery.run_updates(
            tracker=tracker,
            phone_number="2348000000004",
            channel="whatsapp",
            channel_identity="2348000000004",
            inbound_message_id="wamid.44",
            thread_id="whatsapp:2348000000004",
            turn_id="wamid.44",
            enable_initial_typing=True,
        )
    )
    await asyncio.sleep(0.01)
    progress_task.cancel()

    await progress_task

    assert delivery_events == ["sent"]


@pytest.mark.asyncio
async def test_progress_dedupe_keys_are_turn_scoped_by_inbound_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
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
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)

    dedupe_keys: list[str] = []

    async def _enqueue_outbox_say(*args, **kwargs) -> DeliveryAttemptResult:
        metadata = kwargs.get("metadata", {})
        dedupe_keys.append(str(metadata["dedupe_key"]))
        return DeliveryAttemptResult(status="delivered")

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue_outbox_say
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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

    tracker_a = _ProgressTrackerStub(progress_count=0, last_progress_sent_at=None)
    tracker_b = _ProgressTrackerStub(progress_count=0, last_progress_sent_at=None)

    async def _stop_after_first_send(self) -> None:
        self.progress_count = MAX_PROGRESS_MESSAGES

    tracker_a.record_progress_sent = _stop_after_first_send.__get__(tracker_a, _ProgressTrackerStub)
    tracker_b.record_progress_sent = _stop_after_first_send.__get__(tracker_b, _ProgressTrackerStub)

    task_a = asyncio.create_task(
        handler.progress_delivery.run_updates(
            tracker=tracker_a,
            phone_number="2348000000006",
            channel="telegram",
            channel_identity="123",
            inbound_message_id="tg.1",
            thread_id="telegram:2348000000006",
            turn_id="tg.1",
            enable_initial_typing=True,
        )
    )
    task_b = asyncio.create_task(
        handler.progress_delivery.run_updates(
            tracker=tracker_b,
            phone_number="2348000000006",
            channel="telegram",
            channel_identity="123",
            inbound_message_id="tg.2",
            thread_id="telegram:2348000000006",
            turn_id="tg.2",
            enable_initial_typing=True,
        )
    )

    await asyncio.sleep(0.01)
    task_a.cancel()
    task_b.cancel()
    await asyncio.gather(task_a, task_b, return_exceptions=True)

    assert len(dedupe_keys) == 2
    assert dedupe_keys[0] != dedupe_keys[1]
    assert "tg.1" in dedupe_keys[0]
    assert "tg.2" in dedupe_keys[1]


@pytest.mark.asyncio
async def test_deduped_progress_attempt_consumes_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _GraphStub()
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.AsyncRedisSaver",
        lambda redis_client: _CheckpointerStub(),
    )
    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.handler.build_orchestrator_graph",
        lambda checkpointer: graph,
    )
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
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.PROGRESS_SETTLE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr("apps.chat.src.agent.orchestrator.graph.progress_delivery.MAX_PROGRESS_MESSAGES", 1)

    class _SingleSnapshotTracker:
        def __init__(self) -> None:
            self.record_calls = 0
            self.progress_count = 0

        async def snapshot(self) -> TurnProgressSnapshot:
            return TurnProgressSnapshot(
                stage_key="query.fetching_transactions",
                started_at=0.0,
                stage_started_at=0.0,
                last_progress_sent_at=None,
                progress_count=self.progress_count,
                stage_metadata={"scope_label": "what you sent to mum"},
                locale="en",
            )

        async def wait_for_update(self, timeout_seconds: float | None = None) -> None:
            del timeout_seconds
            await asyncio.sleep(0)

        async def record_progress_sent(self) -> None:
            self.record_calls += 1
            self.progress_count += 1

    async def _enqueue_outbox_say(*args, **kwargs) -> DeliveryAttemptResult:
        del args, kwargs
        return DeliveryAttemptResult(status="deduped_completed")

    monkeypatch.setattr(
        "apps.chat.src.agent.orchestrator.graph.progress_delivery.enqueue_outbox_say", _enqueue_outbox_say
    )

    handler = OrchestratorGraphHandler(
        task_planner=SimpleNamespace(),
        semantic_router_llm=SimpleNamespace(),
        capability_classifier_llm=SimpleNamespace(),
        transfer_service=SimpleNamespace(),
        airtime_service=SimpleNamespace(),
        query_service=SimpleNamespace(),
        data_service=SimpleNamespace(),
        account_service=SimpleNamespace(),
        beneficiary_service=SimpleNamespace(),
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

    tracker = _SingleSnapshotTracker()
    progress_task = asyncio.create_task(
        handler.progress_delivery.run_updates(
            tracker=tracker,
            phone_number="2348000000007",
            channel="telegram",
            channel_identity="123",
            inbound_message_id="tg.3",
            thread_id="telegram:2348000000007",
            turn_id="tg.3",
            enable_initial_typing=True,
        )
    )
    await asyncio.wait_for(progress_task, timeout=0.1)

    assert tracker.record_calls == 1
