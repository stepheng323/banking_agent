import time
from datetime import date

import pytest

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.query_surface import (
    build_query_context_for_worker,
    build_query_session_snapshot_from_surface,
    get_active_query_surface,
    query_surface_is_active,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import push_query_surface_frame
from apps.chat.src.agent.orchestrator.workflows.planner.context.query_session.context_query_session import (
    _load_query_session_snapshot,
)
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.transactions.query.contracts import SelectionPayload
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from tests.query.factories import make_query_request


class _RedisStub:
    def __init__(self, payload: str | None = None) -> None:
        self.payload = payload
        self.get_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.payload


def _contract() -> QueryRequest:
    return (
        make_query_request(
            intent=QueryIntent.BENEFICIARY_SUMMARY,
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 28)),
            result_limit=1,
        )
    )


def _query_frame(*, created_at: int | None = None, ttl_seconds: int = 600) -> ContextFrame:
    contract = _contract()
    payload = SelectionPayload(
        selection_kind="beneficiary",
        entity_type="beneficiary",
        entity_id="sender-acme",
        label="Acme Corp",
        filters_patch={"counterparty": ["Acme Corp"]},
        fact_capabilities=["date", "amount", "bank", "reference"],
    )
    return ContextFrame(
        frame_id="query_surface_1",
        frame_type=ContextFrameType.TRANSACTION_DETAIL,
        items=[
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="sender-acme",
                label="Acme Corp",
                selection_payload=payload,
                data={
                    "amount": 950000,
                    "count": 1,
                    "date": "2026-06-28",
                    "bank_name": "Zenith Bank",
                },
            )
        ],
        created_at_ts=created_at if created_at is not None else int(time.time()),
        ttl_seconds=ttl_seconds,
        metadata={
            "source": "query",
            "surface_mode": "direct_answer",
            "summary_text": "Acme Corp sent you the most this month: ₦950,000.",
            "query_request": contract.model_dump(mode="json"),
            "surface_context": {
                "mode": "direct_answer",
                "focus_type": "beneficiary",
                "type": "focused_beneficiary",
            },
        },
    )


def _transaction_list_frame(*, created_at: int | None = None) -> ContextFrame:
    contract = (
        make_query_request(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 29)),
        )
    )
    return ContextFrame(
        frame_id="query_surface_list",
        frame_type=ContextFrameType.TRANSACTION_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-1",
                label="Received from Acme Corp",
                selection_payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-1",
                    label="Received from Acme Corp",
                ),
                data={"amount": 950000, "date": "2026-06-26", "bank_name": "First Bank"},
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-2",
                label="Sent to Mum",
                selection_payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-2",
                    label="Sent to Mum",
                ),
                data={"amount": 50000, "date": "2026-06-26", "bank_name": "GTBank"},
            ),
            ContextEntity(
                entity_type=EntityType.TRANSACTION,
                entity_id="tx-3",
                label="Sent to Dad",
                selection_payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="tx-3",
                    label="Sent to Dad",
                ),
                data={"amount": 30000, "date": "2026-06-25", "bank_name": "First Bank"},
            ),
        ],
        created_at_ts=created_at if created_at is not None else int(time.time()),
        ttl_seconds=600,
        metadata={
            "source": "query",
            "surface_mode": "transaction_list",
            "summary_text": "I found 37 transactions in the last 30 days.",
            "query_request": contract.model_dump(mode="json"),
        },
    )


def test_active_query_surface_uses_latest_non_expired_query_frame() -> None:
    now = int(time.time())
    expired = _query_frame(created_at=now - 1000, ttl_seconds=1)
    active = _query_frame(created_at=now)
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[expired, active],
    )

    assert query_surface_is_active(active, now=now)
    assert not query_surface_is_active(expired, now=now)
    assert get_active_query_surface(state, now=now) == active


def test_query_context_for_worker_builds_surface_context_from_frame() -> None:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[_query_frame()],
    )

    worker_context = build_query_context_for_worker(state)

    operation = worker_context["active_query_surface"]["metadata"]["query_request"]["operation"]
    assert operation["kind"] == "summarize"
    assert operation["summary"]["type"] == "grouped"
    assert operation["summary"]["dimension"] == "counterparty"
    assert worker_context["active_query_surface"]["metadata"]["surface_mode"] == "direct_answer"
    assert worker_context["active_query_surface"]["items"][0]["label"] == "Acme Corp"


def test_query_context_for_worker_preserves_recent_query_frames_before_latest_surface() -> None:
    now = int(time.time())
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[
            _transaction_list_frame(created_at=now - 2),
            _query_frame(created_at=now),
        ],
    )

    worker_context = build_query_context_for_worker(state)

    assert worker_context["active_query_surface"]["metadata"]["surface_mode"] == "direct_answer"
    assert len(worker_context["context_frames"]) == 2
    assert worker_context["context_frames"][0]["metadata"]["surface_mode"] == "transaction_list"
    assert worker_context["context_frames"][0]["items"][2]["entity_id"] == "tx-3"
    assert worker_context["context_frames"][1]["metadata"]["surface_mode"] == "direct_answer"


def test_direct_query_answer_pushes_latest_query_surface_frame() -> None:
    old_list_frame = _query_frame(created_at=int(time.time()) - 10)
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[old_list_frame],
    )
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator({}),
    )
    contract = (
        make_query_request(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 29)),
        )
    )
    result = QueryResult(
        summary_text="You spent ₦1,460,052 this month, across 52 transactions.",
        items=[],
        query_request=contract,
    )

    push_query_surface_frame(ctx, result)

    active = get_active_query_surface(state)
    assert active is not None
    assert active.frame_type == ContextFrameType.GENERIC
    assert active.metadata["surface_mode"] == "direct_answer"
    assert active.metadata["query_request"]["operation"]["kind"] == "summarize"
    assert active.items[0].label == "You spent ₦1,460,052 this month, across 52 transactions."
    worker_context = build_query_context_for_worker(state)
    assert worker_context["active_query_surface"]["metadata"]["query_request"]["operation"]["kind"] == "summarize"


def test_direct_analytics_answer_with_evidence_items_pushes_summary_scope_frame() -> None:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[],
    )
    ctx = ExecutionTurnContext(
        state=state,
        config={"configurable": {}},
        services=OrchestrationServices.from_mapping({}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator({}),
    )
    contract = (
        make_query_request(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 29)),
        )
    )
    result = QueryResult(
        summary_text="You spent ₦75,000 this month, across 2 transactions.",
        items=[
            QueryResultItem(
                id="tx1",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 6, 24),
                metadata={"type": "debit", "counterparty": "Mum"},
            ),
            QueryResultItem(
                id="tx2",
                description="Transfer to Dad",
                amount=25000,
                date=date(2026, 6, 23),
                metadata={"type": "debit", "counterparty": "Dad"},
            ),
        ],
        query_request=contract,
        answer_strategy=QueryAnswerStrategy.DIRECT_ANSWER,
        answer_context=QueryAnswerContext(primary_text="You spent ₦75,000 this month, across 2 transactions."),
    )

    push_query_surface_frame(ctx, result)

    active = get_active_query_surface(state)
    assert active is not None
    assert active.frame_type == ContextFrameType.GENERIC
    assert active.items[0].selection_payload is not None
    assert active.items[0].selection_payload.selection_kind == "summary_scope"
    worker_context = build_query_context_for_worker(state)
    assert worker_context["active_query_surface"]["metadata"]["surface_context"]["focus_type"] == "summary_scope"
    assert worker_context["active_query_surface"]["items"][0]["entity_id"] == "summary_scope"


@pytest.mark.asyncio
async def test_query_session_loader_prefers_context_frame_over_redis() -> None:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[_query_frame()],
    )
    redis = _RedisStub(payload='{"session_active": true}')

    snapshot, source = await _load_query_session_snapshot(state)

    assert source == "context_frame"
    assert isinstance(snapshot, dict)
    operation = snapshot["query_request"]["operation"]
    assert operation["kind"] == "summarize"
    assert operation["summary"]["dimension"] == "counterparty"
    assert redis.get_calls == []


@pytest.mark.asyncio
async def test_query_session_loader_does_not_use_redis_without_context_frame() -> None:
    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[],
    )
    redis = _RedisStub(payload='{"session_active": true}')

    snapshot, source = await _load_query_session_snapshot(state)

    assert snapshot is None
    assert source is None
    assert redis.get_calls == []


def test_invalid_or_expired_frame_does_not_build_query_session() -> None:
    frame = _query_frame(created_at=int(time.time()) - 1000, ttl_seconds=1)
    assert build_query_session_snapshot_from_surface(frame) is not None

    state = OrchestratorState(
        user_id="user-1",
        phone_number="2348000000001",
        context_frames=[frame],
    )
    assert build_query_context_for_worker(state) == {}
