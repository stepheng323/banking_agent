from datetime import date
from typing import Any

import pytest

from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.contracts import SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    Aggregation,
    Filters,
    QueryIntent,
    QueryRequest,
    QueryResult,
    QueryResultItem,
    TimeRange,
)
from banking.transactions.query.models.extraction import (
    PendingClarificationState,
    QueryExtractionResult,
    QueryTimeRange,
    TimeReference,
)
from banking.transactions.query.services.reasoning.models import QuerySemanticDecision
from banking.transactions.query.session import QuerySessionManager, _session_has_surface_view
from banking.transactions.query.worker import QueryWorker
from shared.config.settings import settings
from tests.query.factories import make_query_request


@pytest.fixture(autouse=True)
def _disable_unified_transaction_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_unified_transaction_view", False)


def _query_ir(**kwargs: object) -> QueryRequest:
    fallback_day = date(2026, 3, 28)
    defaults: dict[str, object] = {
        "intent": QueryIntent.TRANSACTION_LIST,
        "time_range": TimeRange(start=fallback_day, end=fallback_day),
    }
    defaults.update(kwargs)
    return make_query_request(**defaults)


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise NotImplementedError


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


class _DummyProvider:
    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return []


class _RecordingProvider:
    def __init__(self, transactions: list[dict[str, Any]]) -> None:
        self.transactions = transactions
        self.calls = 0

    async def get_transactions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        self.calls += 1
        return list(self.transactions)


class _WindowedProvider:
    def __init__(self, transactions_by_account: dict[str, list[dict[str, Any]]]) -> None:
        self.transactions_by_account = transactions_by_account
        self.calls = 0

    async def get_transactions(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del args, kwargs
        self.calls += 1
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        return [
            transaction
            for transaction in self.transactions_by_account.get(account_id, [])
            if start <= date.fromisoformat(str(transaction["date"])) <= end
        ]


class _SessionManager:
    def __init__(self) -> None:
        self.saved_state: dict[str, Any] | None = None

    async def load(self, key: str) -> dict[str, Any] | None:
        del key
        return None

    async def save(self, key: str, state: dict[str, Any]) -> None:
        del key
        self.saved_state = state

    async def clear(self, key: str) -> None:
        del key


class _LoadedSessionManager(_SessionManager):
    def __init__(self, loaded_state: dict[str, Any]) -> None:
        super().__init__()
        self.loaded_state = loaded_state
        self.load_calls: list[str] = []
        self.cleared_key: str | None = None

    async def load(self, key: str) -> dict[str, Any] | None:
        self.load_calls.append(key)
        return dict(self.loaded_state)

    async def clear(self, key: str) -> None:
        self.cleared_key = key


class _ProgressTracker:
    def __init__(self) -> None:
        self.stage_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def set_stage(self, stage_key: str, *, stage_metadata: dict[str, Any] | None = None) -> None:
        self.stage_calls.append((stage_key, stage_metadata))


class _RedisStoreStub:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | None:
        return self.saved.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.saved[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.saved.pop(key, None)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


def _active_session_from_result(result: TransactionResult) -> dict[str, Any]:
    assert result.patch is not None
    session = dict(result.patch)
    session["session_active"] = True
    return session


def _active_surface_context_from_result(result: TransactionResult) -> dict[str, Any]:
    assert result.patch is not None
    query_result = result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    query_request = result.patch["query_request"]
    assert isinstance(query_request, QueryRequest)
    surface_view = query_result.surface_view
    items: list[dict[str, Any]] = []
    if surface_view is not None and surface_view.items:
        for item in surface_view.items:
            items.append(
                {
                    "entity_id": item.id,
                    "entity_type": "query_result",
                    "label": item.label,
                    "data": item.metadata,
                    "selection_payload": item.payload.model_dump(mode="json") if item.payload else None,
                }
            )
    else:
        for item in query_result.items or []:
            items.append(
                {
                    "entity_id": item.id,
                    "entity_type": "transaction",
                    "label": item.description,
                    "data": item.model_dump(mode="json"),
                    "selection_payload": None,
                }
            )
    frame = {
        "frame_id": "query_surface_test",
        "frame_type": "generic",
        "created_at_ts": 1_783_325_953,
        "ttl_seconds": 900,
        "items": items,
        "metadata": {
            "source": "query",
            "query_request": query_request.model_dump(mode="json"),
            "summary_text": query_result.summary_text,
            "surface_mode": surface_view.mode.value if surface_view is not None else "transaction_list",
            "surface_context": surface_view.context if surface_view is not None else {},
        },
    }
    return {"active_query_surface": frame, "context_frames": [frame]}


def _active_surface_context_from_session(session: dict[str, Any]) -> dict[str, Any]:
    raw_contract = session["query_request"]
    query_request = (
        raw_contract
        if isinstance(raw_contract, QueryRequest)
        else QueryRequest.model_validate(raw_contract)
    )
    raw_result = session.get("query_result")
    summary_text = raw_result.get("summary_text") if isinstance(raw_result, dict) else ""
    surface_view = raw_result.get("surface_view") if isinstance(raw_result, dict) else {}
    surface_mode = surface_view.get("mode") if isinstance(surface_view, dict) else "direct_answer"
    frame = {
        "frame_id": "query_surface_test",
        "frame_type": "generic",
        "created_at_ts": 1_783_325_953,
        "ttl_seconds": 900,
        "items": [
            {
                "entity_id": "summary_scope",
                "entity_type": "query_result",
                "label": summary_text or "Query result",
                "data": {},
                "selection_payload": None,
            }
        ],
        "metadata": {
            "source": "query",
            "query_request": query_request.model_dump(mode="json"),
            "summary_text": summary_text,
            "surface_mode": surface_mode or "direct_answer",
            "surface_context": surface_view.get("context") if isinstance(surface_view, dict) else {},
        },
    }
    return {"active_query_surface": frame, "context_frames": [frame]}


def _contract(query: QueryRequest) -> QueryRequest:
    assert query.time_range is not None
    return query.model_copy(deep=True)


@pytest.mark.asyncio
async def test_worker_restores_from_active_query_surface_and_marks_patch() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    active_query_session = {
        "session_active": True,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ).model_dump(),
        "current_page": 0,
    }

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "any credits?"},
        context={
            "phone_number": "2348000000300",
            "user_id": "u1",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 4),
            **_active_surface_context_from_session(active_query_session),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert session_manager.saved_state is None


@pytest.mark.asyncio
async def test_worker_ignores_legacy_stashed_query_session_input() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    # Deliberately pass the removed key to prove legacy successful-session state
    # cannot become canonical query context again.
    legacy_stashed_query_session = {
        "session_active": True,
        "timestamp": 0.0,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 6)),
            )
        ).model_dump(),
        "current_page": 0,
    }

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"] == {}
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "any credits?"},
        context={
            "phone_number": "2348000000308",
            "user_id": "u1",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 4),
            "stashed_query_session": legacy_stashed_query_session,
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch is not None


@pytest.mark.asyncio
async def test_worker_returns_pending_query_clarification_without_redis_persistence() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    pending = PendingClarificationState(
        original_query="How much did I spend last",
        current_intent=QueryIntent.ANALYTICS_SUMMARY,
        original_extraction=QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=QueryTimeRange(reference_type=TimeReference.VAGUE, days_back=30),
            raw_query="How much did I spend last",
        ),
        resolver_message="What time period did you mean by 'last'?",
        language="en",
    )

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            response="What time period did you mean by 'last'?",
            patch={"session_active": True, "pending_clarification": pending},
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much did I spend last"},
        context={
            "phone_number": "2348000000301",
            "user_id": "u2",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
        },
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.patch is not None
    assert result.patch["session_active"] is True
    assert result.patch["pending_clarification"] == pending
    assert session_manager.saved_state is None


@pytest.mark.asyncio
async def test_worker_returns_query_state_without_redis_frame_persistence() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    query_request = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
        )
    )
    query_result = QueryResult(
        summary_text="You spent ₦60,000 this week.",
        interpretation={"intent": "analytics_summary"},
        items=[
            QueryResultItem(
                id="txn-1",
                description="Transfer to Mum",
                amount=50000.0,
                date=date(2026, 3, 18),
            ),
            QueryResultItem(
                id="txn-2",
                description="Transfer to Dad",
                amount=10000.0,
                date=date(2026, 3, 17),
            ),
        ],
        surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY, context={"type": "spending_total"}),
    )

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "query_request": query_request,
                "query_result": query_result,
                "flow_state": "complete",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much did I spend this week"},
        context={
            "phone_number": "2348000000302",
            "user_id": "u3",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert session_manager.saved_state is None
    assert result.patch is not None
    assert result.patch["query_request"] == query_request
    assert result.patch["query_result"] == query_result


def test_session_shape_detects_surface_view() -> None:
    session = {
        "session_active": True,
        "query_result": QueryResult(
            summary_text="You spent ₦60,000 this week.",
            surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY),
        ).model_dump(mode="json"),
    }

    assert _session_has_surface_view(session) is True


@pytest.mark.asyncio
async def test_worker_sets_followup_progress_stage_before_pipeline_run() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    tracker = _ProgressTracker()
    active_query_session = {
        "session_active": True,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
            )
        ).model_dump(),
        "current_page": 0,
    }

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        assert tracker.stage_calls == [
            (
                "query.resolving_followup",
                {
                    "task_type": "query",
                    "task_mix": "generic",
                    "intent_family": "analytics_summary",
                    "direction": "sent",
                    "counterparty_label": "mum",
                    "recipient_display": "Mum",
                    "scope_label": "what you sent to mum",
                },
            )
        ]
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000399",
            "user_id": "u-progress",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_session(active_query_session),
            "progress_tracker": tracker,
        },
    )

    assert result.outcome == TransactionOutcome.OK


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "flow_state": "executing",
                "_query_semantic_decision": "fresh_query",
                "_query_semantic_context_mode": "none",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": None,
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "show my last transaction"},
        context={
            "phone_number": "2348000000302",
            "user_id": "u3",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_turn_summary",
        {
            "context_mode": "fresh",
            "semantic_decision": "fresh_query",
            "semantic_context_mode": "none",
            "semantic_llm_used": True,
            "deterministic_surface_action": None,
            "session_transition": None,
            "outcome": "ok",
            "flow_state": "executing",
            "surface_type": None,
            "session_active": True,
            "has_pending_clarification": False,
            "session_source": "none",
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_for_active_result_fact_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"]["session_active"] is True
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "flow_state": "executing",
                "query_result": QueryResult(
                    summary_text="",
                    surface_view=SurfaceView(
                        mode=SurfaceViewMode.DIRECT_ANSWER,
                        context={"type": "single_transaction"},
                    ),
                ),
                "_query_semantic_decision": "continuation",
                "_query_semantic_context_mode": "active_result",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": "answer_fact_active_result",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "was it successful?"},
        context={
            "phone_number": "2348000000303",
            "user_id": "u4",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
            **_active_surface_context_from_session(
                {
                    "session_active": True,
                    "query_request": _contract(
                        _query_ir(
                            intent=QueryIntent.TRANSACTION_SEARCH,
                            time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                        )
                    ).model_dump(),
                    "query_result": {
                        "items": [
                            {
                                "id": "txn-1",
                                "description": "Payment to Mum",
                                "amount": 10000.0,
                                "date": "2026-03-13",
                                "metadata": {"status": "processing"},
                            }
                        ],
                        "surface_view": {"mode": "direct_answer", "context": {"type": "single_transaction"}},
                    },
                }
            ),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_transition",
        {
            "transition": "answer_fact_active_result",
            "context_mode": "active_result",
            "semantic_decision": "continuation",
            "session_active": True,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_for_conversational_active_result_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"]["session_active"] is True
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="It is on the high side.\n\nYou can ask what made it up.",
            patch={
                "session_active": False,
                "flow_state": "complete",
                "query_result": QueryResult(
                    summary_text="You spent ₦10,000 yesterday.",
                    surface_view=SurfaceView(
                        mode=SurfaceViewMode.GROUPED_SUMMARY,
                        context={"type": "spending_total"},
                    ),
                ),
                "_query_semantic_decision": "continuation",
                "_query_semantic_context_mode": "active_result",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": "exit_query_session_conversational",
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "That's a lot"},
        context={
            "phone_number": "2348000000304",
            "user_id": "u5",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
            **_active_surface_context_from_session(
                {
                    "session_active": True,
                    "query_request": _contract(
                        _query_ir(
                            intent=QueryIntent.ANALYTICS_SUMMARY,
                            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 13)),
                        )
                    ).model_dump(),
                    "query_result": {
                        "summary_text": "You spent ₦10,000 yesterday.",
                        "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
                    },
                }
            ),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_transition",
        {
            "transition": "exit_query_session_conversational",
            "context_mode": "active_result",
            "semantic_decision": "continuation",
            "session_active": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_query_turn_summary_from_surface_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "session_active": True,
                "flow_state": "complete",
                "query_result": QueryResult(
                    summary_text="You spent ₦10,000 this week.",
                    surface_view=SurfaceView(mode=SurfaceViewMode.GROUPED_SUMMARY),
                ),
                "_query_semantic_decision": "fresh_query",
                "_query_semantic_context_mode": "none",
                "_query_semantic_llm_used": True,
                "_query_deterministic_surface_action": None,
                "_query_session_transition": None,
            },
        )

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "how much did i spend"},
        context={
            "phone_number": "2348000000305",
            "user_id": "u6",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 13),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_turn_summary",
        {
            "context_mode": "fresh",
            "semantic_decision": "fresh_query",
            "semantic_context_mode": "none",
            "semantic_llm_used": True,
            "deterministic_surface_action": None,
            "session_transition": None,
            "outcome": "ok",
            "flow_state": "complete",
            "surface_type": "summary",
            "session_active": True,
            "has_pending_clarification": False,
            "session_source": "none",
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_logs_active_surface_session_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.info", _capture)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": True})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    active_query_session = {
        "session_active": True,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            )
        ).model_dump(),
        "query_result": {
            "summary_text": "You spent ₦60,000 this week.",
            "items": [],
            "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
        },
        "query_frames": [],
    }

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000310",
            "user_id": "u-log-shape",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_session(active_query_session),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert (
        "query_session_loaded",
        {
            "session_source": "orchestrator_context",
            "session_active": True,
            "has_query_request": True,
            "has_query_result": True,
            "has_surface": True,
            "has_query_frames": True,
            "has_pending_clarification": False,
        },
    ) in events


@pytest.mark.asyncio
async def test_worker_does_not_load_redis_query_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = _LoadedSessionManager(
        {
            "session_active": True,
            "query_result": {
                "summary_text": "Earlier summary",
                "items": [],
                "surface_view": {"mode": "grouped_summary", "context": {"type": "spending_total"}},
            },
            "query_frames": [],
        }
    )
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    warnings: list[tuple[str, dict[str, Any]]] = []

    def _capture_warning(event: str, **kwargs: Any) -> None:
        warnings.append((event, kwargs))

    monkeypatch.setattr("banking.transactions.query.worker.logger.warning", _capture_warning)

    async def _fake_pipeline_run(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        assert state["query_session"] == {}
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"session_active": False})

    worker.pipeline.run = _fake_pipeline_run  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000311",
            "user_id": "u-missing-contract",
            "accounts": [],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert session_manager.load_calls == []
    assert session_manager.cleared_key == "query:session:2348000000311"
    assert warnings == []


@pytest.mark.asyncio
async def test_worker_recovers_ambiguous_last_week_followup_from_stashed_session() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    active_query_session = {
        "session_active": True,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                aggregation=Aggregation(type="sum"),
            )
        ).model_dump(),
        "query_result": {"summary_text": "You spent ₦60,000 on mum this week.", "items": []},
        "current_page": 1,
        "show_expanded": True,
    }

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="unclear",
            followup_intent="none",
            confidence=0.21,
            reason="ambiguous_followup",
        )

    captured_ranges: list[tuple[date, date]] = []

    async def _fake_execute(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        query_request = state["query_request"]
        if isinstance(query_request, dict):
            query_request = QueryRequest.model_validate(query_request)
        captured_ranges.append((query_request.time_start, query_request.time_end))
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Recovered last week summary.",
            patch={
                "session_active": True,
                "query_request": query_request,
                "query_result": QueryResult(summary_text="Recovered last week summary.", items=[]),
                "flow_state": "complete",
            },
        )

    worker.extractor.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    worker.executor.run = _fake_execute  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "What about last week"},
        context={
            "phone_number": "2348000000312",
            "user_id": "u-worker-recovery",
            "accounts": [{"account_id": "acc_1"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_session(active_query_session),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert captured_ranges == [(date(2026, 3, 9), date(2026, 3, 15))]


@pytest.mark.asyncio
async def test_worker_reuses_active_query_scope_for_how_much_total_followup() -> None:
    session_manager = _SessionManager()
    worker = QueryWorker(_DummyLLM(), _DummyProvider(), session_manager)  # type: ignore[arg-type]
    active_query_session = {
        "session_active": True,
        "query_request": _contract(
            _query_ir(
                intent=QueryIntent.TRANSACTION_LIST,
                time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19), granularity="month"),
                filters=Filters(transaction_type="debit", merchant=["mum"]),
                result_limit=5,
                result_reference="latest",
            )
        ).model_dump(),
        "query_result": {"summary_text": "Transactions to Mum this month.", "items": []},
        "current_page": 0,
        "show_expanded": False,
    }

    async def _fake_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            followup_intent="refine_existing",
            confidence=0.94,
            reason="llm_total_followup",
        )

    captured_contracts: list[QueryRequest] = []

    async def _fake_execute(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del worker_context
        query_request = state["query_request"]
        if isinstance(query_request, dict):
            query_request = QueryRequest.model_validate(query_request)
        captured_contracts.append(query_request)
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Total sent to Mum this month.",
            patch={
                "session_active": True,
                "query_request": query_request,
                "query_result": QueryResult(summary_text="Total sent to Mum this month.", items=[]),
                "flow_state": "complete",
            },
        )

    worker.extractor.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    worker.executor.run = _fake_execute  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How much total"},
        context={
            "phone_number": "2348000000313",
            "user_id": "u-worker-total",
            "accounts": [{"account_id": "acc_1"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_session(active_query_session),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert len(captured_contracts) == 1
    query_request = captured_contracts[0]
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 1)
    assert query_request.time_end == date(2026, 3, 19)
    assert query_request.filters is not None
    assert query_request.filters.transaction_type == "debit"
    assert query_request.filters.merchant == ["mum"]
    assert query_request.aggregation is not None
    assert query_request.aggregation.type == "sum"


@pytest.mark.asyncio
async def test_worker_reuses_context_scope_for_time_delta_followup() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _RecordingProvider(
        transactions=[
            {
                "id": "tx-old",
                "narration": "Salary payment",
                "amount": 100000,
                "date": "2026-03-01",
                "type": "credit",
            },
            {
                "id": "tx-recent",
                "narration": "Refund",
                "amount": 5000,
                "date": "2026-03-04",
                "type": "credit",
            },
            {
                "id": "tx-debit",
                "narration": "Card payment",
                "amount": 2500,
                "date": "2026-03-04",
                "type": "debit",
            },
        ]
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4), granularity="day"),
            filters=Filters(transaction_type="credit"),
            result_limit=5,
            result_reference="latest",
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Show my incoming transactions"},
        context={
            "phone_number": "2348000000314",
            "user_id": "u-worker-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 4), end=date(2026, 3, 4), granularity="day"),
            delta_type="time",
            confidence=0.98,
            reason="llm_same_scope_narrower_window",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about today?"},
        context={
            "phone_number": "2348000000314",
            "user_id": "u-worker-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
            **_active_surface_context_from_result(first_result),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2
    assert second_result.patch is not None
    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.cache_reused is False
    assert [item.description for item in query_result.items or []] == ["Refund"]


@pytest.mark.asyncio
async def test_worker_reuses_context_scope_for_filter_delta_followup() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _RecordingProvider(
        transactions=[
            {
                "id": "tx-credit",
                "narration": "Salary payment",
                "amount": 100000,
                "date": "2026-03-04",
                "type": "credit",
            },
            {
                "id": "tx-debit",
                "narration": "Card payment",
                "amount": 2500,
                "date": "2026-03-04",
                "type": "debit",
            },
        ]
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 4), granularity="day"),
            result_limit=5,
            result_reference="latest",
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Show my transactions"},
        context={
            "phone_number": "2348000000317",
            "user_id": "u-worker-filter-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="filter_delta",
            followup_intent="refine_existing",
            delta_type="filter",
            filters=Filters(min_amount=3000),
            confidence=0.98,
            reason="llm_credit_filter_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "Only above 3k"},
        context={
            "phone_number": "2348000000317",
            "user_id": "u-worker-filter-cache",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 4),
            **_active_surface_context_from_result(first_result),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2
    assert second_result.patch is not None
    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.cache_reused is False
    assert [item.description for item in query_result.items or []] == ["Salary payment"]


@pytest.mark.asyncio
async def test_worker_restores_context_analytics_followup_for_time_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("banking.transactions.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 19))

    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider(
        {
            "acc_1": [
                {"id": "tx-1", "narration": "Card purchase", "amount": 5000, "date": "2026-03-19", "type": "debit"},
                {"id": "tx-2", "narration": "Fuel", "amount": 7000, "date": "2026-03-18", "type": "debit"},
                {"id": "tx-3", "narration": "Salary", "amount": 95000, "date": "2026-03-17", "type": "credit"},
            ]
        }
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 19), granularity="month"),
            filters=Filters(transaction_type="debit"),
            aggregation=Aggregation(type="sum"),
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "How much did I spend this month?"},
        context={
            "phone_number": "2348000000315",
            "user_id": "u-worker-analytics",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 1

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
            delta_type="time",
            confidence=0.98,
            reason="llm_today_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about today?"},
        context={
            "phone_number": "2348000000315",
            "user_id": "u-worker-analytics",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_result(first_result),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2
    assert second_result.patch is not None
    query_request = second_result.patch["query_request"]
    assert isinstance(query_request, QueryRequest)
    assert query_request.intent == QueryIntent.ANALYTICS_SUMMARY
    assert query_request.time_start == date(2026, 3, 19)
    assert query_request.time_end == date(2026, 3, 19)

    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert query_result.summary_text == "You spent ₦5,000 today, across 1 transaction."
    assert [item.description for item in query_result.items or []] == ["Card purchase"]


@pytest.mark.asyncio
async def test_worker_count_time_delta_followup_renders_yesterday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("banking.transactions.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 19))

    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider(
        {
            "acc_1": [
                {"id": "today-1", "narration": "Card purchase", "amount": 5000, "date": "2026-03-19", "type": "debit"},
                {"id": "yday-1", "narration": "Fuel", "amount": 7000, "date": "2026-03-18", "type": "debit"},
                {"id": "yday-2", "narration": "Groceries", "amount": 2000, "date": "2026-03-18", "type": "debit"},
            ]
        }
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
            aggregation=Aggregation(type="count"),
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "How many transactions have I carried out today"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-count-yesterday",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert first_result.response == "You made *1* transaction today."

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            delta_type="time",
            confidence=0.99,
            reason="llm_yesterday_followup_without_concrete_range",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about yesterday"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-count-yesterday",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_result(first_result),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert second_result.response == "You made *2* transactions yesterday."
    assert second_result.patch is not None
    query_request = second_result.patch["query_request"]
    assert isinstance(query_request, QueryRequest)
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)

    show_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _show_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="fresh_query",
            continuation_type="unclear",
            confidence=0.42,
            reason="llm_mislabeled_show_existing_transactions_as_fresh_query",
        )

    show_worker.extractor.reasoner.reason = _show_reason  # type: ignore[method-assign]

    third_result = await show_worker.run(
        payload={"message": "Show them"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-count-yesterday",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_result(second_result),
        },
    )

    assert third_result.outcome == TransactionOutcome.OK
    assert third_result.patch is not None
    query_request = third_result.patch["query_request"]
    assert isinstance(query_request, QueryRequest)
    assert query_request.intent == QueryIntent.TRANSACTION_LIST
    assert query_request.time_start == date(2026, 3, 18)
    assert query_request.time_end == date(2026, 3, 18)
    query_result = third_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    assert {item.description for item in query_result.items or []} == {"Fuel", "Groceries"}
    assert "today" not in (third_result.response or "").lower()


@pytest.mark.asyncio
async def test_worker_count_zero_summary_uses_natural_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("banking.transactions.query.handlers.analytics.lagos_today", lambda: date(2026, 3, 19))

    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider({"acc_1": []})
    worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date(2026, 3, 19), end=date(2026, 3, 19), granularity="day"),
            aggregation=Aggregation(type="count"),
        )
    )

    async def _extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    worker.extractor.run = _extract  # type: ignore[method-assign]

    result = await worker.run(
        payload={"message": "How many transactions have I carried out today"},
        context={
            "phone_number": "2348000000317",
            "user_id": "u-worker-count-zero",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You didn't make any transactions today."


@pytest.mark.asyncio
async def test_worker_restores_context_time_comparison_followup_for_time_delta() -> None:
    redis = _RedisStoreStub()
    session_manager = QuerySessionManager(redis)  # type: ignore[arg-type]
    provider = _WindowedProvider(
        {
            "acc_1": [
                {"id": "cmp-1", "narration": "Groceries", "amount": 2000, "date": "2026-03-03", "type": "debit"},
                {"id": "cmp-2", "narration": "Fuel", "amount": 3000, "date": "2026-03-05", "type": "debit"},
                {"id": "cur-1", "narration": "Travel", "amount": 6000, "date": "2026-03-10", "type": "debit"},
                {"id": "cur-2", "narration": "Food", "amount": 2000, "date": "2026-03-12", "type": "debit"},
                {"id": "init-cmp", "narration": "Bills", "amount": 1000, "date": "2026-03-13", "type": "debit"},
                {"id": "init-cur", "narration": "Transfer", "amount": 4000, "date": "2026-03-17", "type": "debit"},
            ]
        }
    )
    initial_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]
    initial_contract = _contract(
        _query_ir(
            intent=QueryIntent.TIME_COMPARISON,
            time_range=TimeRange(start=date(2026, 3, 16), end=date(2026, 3, 19), granularity="week"),
            filters=Filters(transaction_type="debit"),
        )
    )

    async def _initial_extract(state: dict[str, Any], worker_context: Any) -> TransactionResult:
        del state, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch={
                "flow_state": "executing",
                "query_request": initial_contract,
            },
        )

    initial_worker.extractor.run = _initial_extract  # type: ignore[method-assign]

    first_result = await initial_worker.run(
        payload={"message": "Compare this week to the previous period"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-comparison",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
        },
    )

    assert first_result.outcome == TransactionOutcome.OK
    assert provider.calls == 2

    followup_worker = QueryWorker(_DummyLLM(), provider, session_manager)  # type: ignore[arg-type]

    async def _followup_reason(_: object) -> QuerySemanticDecision:
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="time_delta",
            followup_intent="replace_scope",
            time_range=TimeRange(start=date(2026, 3, 9), end=date(2026, 3, 15), granularity="week"),
            delta_type="time",
            confidence=0.98,
            reason="llm_last_week_comparison_followup",
        )

    followup_worker.extractor.reasoner.reason = _followup_reason  # type: ignore[method-assign]

    second_result = await followup_worker.run(
        payload={"message": "What about last week?"},
        context={
            "phone_number": "2348000000316",
            "user_id": "u-worker-comparison",
            "accounts": [{"account_id": "acc_1", "bank_name": "First Bank"}],
            "language": "en",
            "today": date(2026, 3, 19),
            **_active_surface_context_from_result(first_result),
        },
    )

    assert second_result.outcome == TransactionOutcome.OK
    assert provider.calls == 4
    assert second_result.patch is not None
    query_request = second_result.patch["query_request"]
    assert isinstance(query_request, QueryRequest)
    assert query_request.intent == QueryIntent.TIME_COMPARISON
    assert query_request.time_start == date(2026, 3, 9)
    assert query_request.time_end == date(2026, 3, 15)

    query_result = second_result.patch["query_result"]
    assert isinstance(query_result, QueryResult)
    spending_item = next(item for item in query_result.items or [] if item.id == "spending")
    assert spending_item.metadata == {
        "current": 9000.0,
        "comparison": 5000.0,
        "change": 4000.0,
        "pct_change": 80.0,
    }
